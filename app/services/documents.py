from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import pytesseract
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import (
    DeductionSource,
    Document,
    DocumentStatus,
    DocumentType,
    EntryType,
    FinancialEntry,
    Installment,
    InstallmentPlan,
    PayslipDeduction,
    PlanType,
    User,
)


AMOUNT_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")
INSTALLMENT_PATTERN = re.compile(r"(?P<label>[\w\s\-/]+?)\s+(?P<current>\d{1,2})/(?P<total>\d{1,2})\s+(?P<amount>\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
COMPETENCE_PATTERN = re.compile(r"(?:competencia|referencia|periodo)\s*[:\-]?\s*([0-1]?\d[/\-]\d{4})", re.IGNORECASE)
NAME_PATTERN = re.compile(r"(?:nome|colaborador|funcionario)\s*[:\-]?\s*(.+)", re.IGNORECASE)
COMPANY_PATTERN = re.compile(r"(?:empresa|empregador)\s*[:\-]?\s*(.+)", re.IGNORECASE)
EMPLOYEE_CODE_PATTERN = re.compile(r"^\d+\s*-\s*([A-ZÀ-ÿ\s]+)$")
MONTH_YEAR_TEXT_PATTERN = re.compile(
    r"(janeiro|fevereiro|marco|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)[/\-\s]+(\d{4})",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
RECEIPT_TOTAL_KEYWORDS = [
    "valor total",
    "total r$",
    "total",
    "vl total",
]

PAYSLIP_FIELD_KEYWORDS = {
    "gross_income": ["salario bruto", "bruto", "total vencimentos", "total proventos", "proventos", "vencimentos"],
    "net_income": ["liquido", "salario liquido", "valor liquido", "liquido a receber", "total liquido"],
    "discounts": ["total descontos", "descontos", "desconto"],
    "inss": ["inss"],
    "irrf": ["irrf", "imposto de renda"],
    "vt": ["vale transporte", "v.t.", "vt"],
    "vr": ["vale refeicao", "vale alimentacao", "v.r.", "vr", "va"],
}
PAYSLIP_DEDUCTION_KEYWORDS = [
    "inss",
    "irrf",
    "imposto",
    "vale",
    "adiantamento",
    "odonto",
    "odonto",
    "saude",
    "sindicato",
    "seguro",
    "pensao",
    "emprestimo",
    "farmacia",
    "desconto",
]
PAYSLIP_EARNING_KEYWORDS = [
    "salario base",
    "hora extra",
    "gratificacao",
    "comissao",
    "adicional",
    "ferias",
    "abono",
    "periculosidade",
    "insalubridade",
]


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower().strip()


_MAX_OCR_PIXELS = 2_000_000  # ~1414×1414 — enough for receipts, safe on free tier


def _prepare_image_for_ocr(image: Image.Image) -> Image.Image:
    """Resize oversized images and convert to grayscale before sending to Tesseract.

    WhatsApp / phone camera images can exceed 12 MP. Running Tesseract on the
    full resolution exhausts memory on constrained hosts (Railway free tier)
    and causes the worker process to be killed, leaving documents stuck in
    PENDING. Resizing to ≤2 MP preserves OCR quality for printed text while
    keeping memory usage low.
    """
    if image.mode not in ("RGB", "L", "RGBA"):
        image = image.convert("RGB")
    w, h = image.size
    if w * h > _MAX_OCR_PIXELS:
        scale = (_MAX_OCR_PIXELS / (w * h)) ** 0.5
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return image.convert("L")  # grayscale improves Tesseract accuracy on receipts


def extract_text_from_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
        return extract_text_from_scanned_pdf(path)
    image = _prepare_image_for_ocr(Image.open(path))
    try:
        return pytesseract.image_to_string(image, lang="por+eng")
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(image, lang="eng")


def extract_text_from_scanned_pdf(path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        target_prefix = Path(tmpdir) / "page"
        command = [
            "pdftoppm",
            "-png",
            str(path),
            str(target_prefix),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        pages = sorted(Path(tmpdir).glob("page-*.png"))
        chunks: list[str] = []
        for page in pages:
            image = Image.open(page)
            try:
                chunk = pytesseract.image_to_string(image, lang="por+eng")
            except pytesseract.TesseractError:
                chunk = pytesseract.image_to_string(image, lang="eng")
            if chunk.strip():
                chunks.append(chunk)
        return "\n".join(chunks)


def parse_brazilian_amount(raw: str) -> float:
    return float(raw.replace(".", "").replace(",", "."))


def categorize_merchant(title: str) -> str:
    value = title.lower()
    if any(token in value for token in ["mercado", "super", "atac", "carrefour"]):
        return "Mercado"
    if any(token in value for token in ["uber", "99", "posto", "combustivel"]):
        return "Transporte"
    if any(token in value for token in ["netflix", "spotify", "cinema"]):
        return "Lazer"
    if any(token in value for token in ["farm", "drog", "saude"]):
        return "Saude"
    return "Cartao"


def extract_amount_from_line(line: str) -> float | None:
    matches = AMOUNT_PATTERN.findall(line)
    if not matches:
        return None
    return parse_brazilian_amount(matches[-1])


def detect_line_value(lines: list[str], keywords: list[str]) -> float | None:
    for line in lines:
        normalized_line = normalize_text(line)
        if any(keyword in normalized_line for keyword in keywords):
            amount = extract_amount_from_line(line)
            if amount is not None:
                return amount
    return None


def extract_named_field(text: str, pattern: re.Pattern[str]) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.search(line)
        if match:
            value = match.group(1).strip(" :-")
            if value:
                return value[:160]
    return None


def infer_company_name(lines: list[str]) -> str | None:
    for line in lines[:8]:
        cleaned = line.strip()
        normalized = normalize_text(cleaned)
        if not cleaned:
            continue
        if any(token in normalized for token in ["ltda", "sa", "s/a", "eireli", "me", "informatica"]):
            return cleaned[:160]
        if "contracheque" in normalized or "folha" in normalized:
            continue
        if re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", cleaned):
            continue
        if len(cleaned) < 6:
            continue
        alpha_ratio = sum(char.isalpha() for char in cleaned) / max(len(cleaned), 1)
        if alpha_ratio < 0.45:
            continue
        if len(cleaned) >= 5 and any(char.isalpha() for char in cleaned):
            return cleaned[:160]
    return None


def parse_date_br(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


def infer_receipt_merchant(lines: list[str]) -> str | None:
    for line in lines[:8]:
        cleaned = normalize_spaces(line)
        normalized = normalize_text(cleaned)
        if not cleaned:
            continue
        if any(token in normalized for token in ["documento auxiliar", "consumidor eletronica", "consulte pela chave", "qtd.total"]):
            continue
        if re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", cleaned):
            continue
        if any(char.isalpha() for char in cleaned) and len(cleaned) >= 6:
            return cleaned[:160]
    return None


def extract_receipt_total(lines: list[str]) -> float | None:
    for line in lines:
        normalized = normalize_text(line)
        if any(keyword in normalized for keyword in RECEIPT_TOTAL_KEYWORDS):
            amount = extract_amount_from_line(line)
            if amount is not None:
                return amount
    return None


def extract_receipt_date(lines: list[str]) -> date | None:
    for line in lines:
        match = DATE_PATTERN.search(line)
        if match:
            parsed = parse_date_br(match.group(1))
            if parsed:
                return parsed
    return None


def extract_receipt_items(lines: list[str]) -> list[dict]:
    items: list[dict] = []
    for line in lines:
        normalized = normalize_text(line)
        if "valor total" in normalized or "forma de pagamento" in normalized:
            break
        amount = extract_amount_from_line(line)
        if amount is None:
            continue
        if not re.search(r"\b\d{3,}\b", line):
            continue
        label = normalize_spaces(AMOUNT_PATTERN.sub("", line))
        if len(label) < 4:
            continue
        items.append({"label": label[:160], "amount": amount})
    return items[:10]


def extract_receipt_data(text: str, filename: str, document_type: DocumentType) -> tuple[dict, float]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    total = extract_receipt_total(lines)
    items = extract_receipt_items(lines)
    if total is None and items:
        total = round(sum(item["amount"] for item in items), 2)

    merchant = infer_receipt_merchant(lines) or filename
    occurred_on = extract_receipt_date(lines)
    summary = {
        "merchant": merchant,
        "detected_total": total,
        "occurred_on": occurred_on.isoformat() if occurred_on else None,
        "document_kind": "credit_card" if document_type == DocumentType.CREDIT_CARD else "receipt",
    }
    confidence = 0.78 if total is not None else 0.45
    return {"summary": summary, "items": items}, confidence


def infer_employee_name(lines: list[str]) -> str | None:
    for line in lines:
        match = EMPLOYEE_CODE_PATTERN.match(line.strip())
        if match:
            return match.group(1).strip()[:160]
    return None


def infer_competence(text: str, lines: list[str]) -> str | None:
    normalized_text = normalize_text(text)
    competence_match = COMPETENCE_PATTERN.search(normalized_text)
    if competence_match:
        return competence_match.group(1)
    for line in lines:
        normalized_line = normalize_text(line)
        if "folha" not in normalized_line:
            continue
        month_match = MONTH_YEAR_TEXT_PATTERN.search(normalized_line)
        if month_match:
            month_name = month_match.group(1).replace("ç", "c")
            year = month_match.group(2)
            month_map = {
                "janeiro": "01",
                "fevereiro": "02",
                "marco": "03",
                "abril": "04",
                "maio": "05",
                "junho": "06",
                "julho": "07",
                "agosto": "08",
                "setembro": "09",
                "outubro": "10",
                "novembro": "11",
                "dezembro": "12",
            }
            month = month_map.get(month_name)
            if month:
                return f"{month}/{year}"
    return None


def extract_totals_block(lines: list[str]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for index, line in enumerate(lines):
        normalized_line = normalize_text(line)
        if "total de vencimentos" in normalized_line and "liquido" in normalized_line:
            candidate_lines = [line]
            if index + 1 < len(lines):
                candidate_lines.append(lines[index + 1])
            for candidate in candidate_lines:
                amounts = [parse_brazilian_amount(item) for item in AMOUNT_PATTERN.findall(candidate)]
                if len(amounts) >= 3:
                    totals["gross_income"] = amounts[0]
                    totals["discounts"] = amounts[1]
                    totals["net_income"] = amounts[2]
                    if len(amounts) >= 4:
                        totals["fgts"] = amounts[3]
                    return totals
    return totals


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -:%")


def clean_payslip_item_label(raw_line: str) -> str:
    line = re.sub(r"^\d+\s+", "", raw_line).strip()
    line = AMOUNT_PATTERN.sub("", line)
    line = re.sub(r"\b\d+\b", "", line)
    line = line.replace("%", " ")
    return normalize_spaces(line)


def extract_payslip_deduction_items(lines: list[str]) -> list[dict]:
    items: list[dict] = []
    inside_table = False
    for line in lines:
        normalized_line = normalize_text(line)
        if "rubrica" in normalized_line and "descricao" in normalized_line:
            inside_table = True
            continue
        if not inside_table:
            continue
        if "total de vencimentos" in normalized_line:
            break
        if not re.match(r"^\d+\s+", line):
            continue

        code_match = re.match(r"^(\d+)\s+", line)
        code = int(code_match.group(1)) if code_match else None
        amount = extract_amount_from_line(line)
        label = clean_payslip_item_label(line)
        normalized_label = normalize_text(label)

        if not label or amount is None:
            continue
        if any(keyword in normalized_label for keyword in PAYSLIP_EARNING_KEYWORDS):
            continue

        is_deduction = code is not None and code >= 500
        if not is_deduction and any(keyword in normalized_label for keyword in PAYSLIP_DEDUCTION_KEYWORDS):
            is_deduction = True
        if not is_deduction:
            continue

        items.append({"label": label[:120], "amount": amount})
    return items


def extract_payslip_data(text: str, filename: str) -> tuple[dict, float]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    amounts = [parse_brazilian_amount(item) for item in AMOUNT_PATTERN.findall(text)]
    totals_block = extract_totals_block(lines)
    deduction_items = extract_payslip_deduction_items(lines)

    summary: dict[str, float | str | None] = {
        "employee_name": extract_named_field(text, NAME_PATTERN) or infer_employee_name(lines),
        "company_name": extract_named_field(text, COMPANY_PATTERN) or infer_company_name(lines),
        "competence": infer_competence(text, lines),
        "gross_income": None,
        "net_income": None,
        "discounts": None,
        "inss": None,
        "irrf": None,
        "vt": None,
        "vr": None,
    }

    for key in ("gross_income", "net_income", "discounts"):
        if key in totals_block:
            summary[key] = totals_block[key]

    for field, keywords in PAYSLIP_FIELD_KEYWORDS.items():
        if summary[field] is None:
            summary[field] = detect_line_value(lines, keywords)

    if summary["inss"] is None:
        summary["inss"] = next((item["amount"] for item in deduction_items if "inss" in normalize_text(item["label"])), None)
    if summary["irrf"] is None:
        summary["irrf"] = next((item["amount"] for item in deduction_items if "irrf" in normalize_text(item["label"])), None)
    if summary["vr"] is None:
        summary["vr"] = next(
            (
                item["amount"]
                for item in deduction_items
                if any(token in normalize_text(item["label"]) for token in ["vale refeicao", "vale alimentacao", "vr", "va"])
            ),
            None,
        )
    if summary["vt"] is None:
        summary["vt"] = next((item["amount"] for item in deduction_items if "vale transporte" in normalize_text(item["label"]) or normalize_text(item["label"]) == "vt"), None)

    if summary["gross_income"] is None and summary["net_income"] is not None and summary["discounts"] is not None:
        summary["gross_income"] = round(float(summary["net_income"]) + float(summary["discounts"]), 2)

    if summary["net_income"] is None:
        for line in lines:
            normalized_line = normalize_text(line)
            if "liquido" in normalized_line:
                amount = extract_amount_from_line(line)
                if amount is not None:
                    summary["net_income"] = amount
                    break

    if summary["net_income"] is None and amounts:
        summary["net_income"] = max(amounts)

    if summary["discounts"] is None:
        discount_components = [summary[key] for key in ("inss", "irrf", "vt", "vr") if isinstance(summary[key], (int, float))]
        if discount_components:
            summary["discounts"] = round(sum(float(item) for item in discount_components), 2)

    if summary["gross_income"] is None and amounts:
        top_amounts = sorted(amounts, reverse=True)
        candidate = top_amounts[1] if len(top_amounts) > 1 and summary["net_income"] == top_amounts[0] else top_amounts[0]
        if candidate >= float(summary["net_income"] or 0):
            summary["gross_income"] = candidate

    filled_numeric_fields = sum(1 for key in ("gross_income", "net_income", "discounts", "inss", "irrf", "vt", "vr") if isinstance(summary[key], (int, float)))
    confidence = min(0.35 + (filled_numeric_fields * 0.08), 0.92)

    extracted = {
        "document_kind": "payslip",
        "filename": filename,
        "summary": summary,
        "items": deduction_items,
    }
    return extracted, confidence


def cleanup_generated_payslip_entries(db: Session, document: Document, summary: dict) -> None:
    candidate_titles = {
        f"Holerite {document.filename}",
        f"Holerite {summary.get('competence') or document.filename}",
    }
    db.execute(
        delete(FinancialEntry).where(
            FinancialEntry.tenant_id == document.tenant_id,
            FinancialEntry.user_id == document.user_id,
            FinancialEntry.source == "upload",
            FinancialEntry.category == "Salario",
            FinancialEntry.title.in_(candidate_titles),
        )
    )


def cleanup_generated_payslip_deductions(db: Session, document: Document) -> None:
    db.execute(
        delete(PayslipDeduction).where(
            PayslipDeduction.tenant_id == document.tenant_id,
            PayslipDeduction.user_id == document.user_id,
            PayslipDeduction.document_id == document.id,
            PayslipDeduction.source == DeductionSource.UPLOAD,
        )
    )


def store_payslip_deductions(db: Session, document: Document, summary: dict, items: list[dict]) -> None:
    competence = summary.get("competence")
    for item in items:
        label = item.get("label")
        amount = item.get("amount")
        if not label or not isinstance(amount, (int, float)) or amount <= 0:
            continue
        db.add(
            PayslipDeduction(
                tenant_id=document.tenant_id,
                user_id=document.user_id,
                document_id=document.id,
                label=label,
                amount=amount,
                competence=competence,
                occurred_on=datetime.utcnow().date(),
                source=DeductionSource.UPLOAD,
            )
        )


def sync_payslip_outputs(db: Session, document: Document, extracted_data: dict) -> None:
    summary = extracted_data.get("summary") or {}
    cleanup_generated_payslip_deductions(db, document)
    store_payslip_deductions(db, document, summary, extracted_data.get("items", []))
    cleanup_generated_payslip_entries(db, document, summary)
    net_amount = summary.get("net_income") or 0.0
    if net_amount:
        db.add(
            FinancialEntry(
                tenant_id=document.tenant_id,
                user_id=document.user_id,
                title=f"Holerite {summary.get('competence') or document.filename}",
                category="Salario",
                entry_type=EntryType.INCOME,
                amount=net_amount,
                occurred_on=datetime.utcnow().date(),
                source="upload",
                notes=f"Gerado por extracao automatica do holerite (document_id={document.id})",
            )
        )


def process_document(db: Session, document_id: int) -> None:
    document = db.get(Document, document_id)
    if not document:
        return

    try:
        text = extract_text_from_file(Path(document.stored_path))
        extracted_data: dict = {"summary": {}, "items": []}
        confidence = 0.45

        if document.document_type == DocumentType.PAYSLIP:
            extracted_data, confidence = extract_payslip_data(text, document.filename)
            sync_payslip_outputs(db, document, extracted_data)

        elif document.document_type in {DocumentType.CREDIT_CARD, DocumentType.RECEIPT, DocumentType.OTHER}:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            for line in lines:
                match = INSTALLMENT_PATTERN.search(line)
                if match:
                    total = int(match.group("total"))
                    current = int(match.group("current"))
                    amount = parse_brazilian_amount(match.group("amount"))
                    title = match.group("label").strip()[:160]
                    if current == 1:
                        plan = InstallmentPlan(
                            tenant_id=document.tenant_id,
                            user_id=document.user_id,
                            title=title or "Compra parcelada",
                            merchant=title or "Compra parcelada",
                            plan_type=PlanType.INSTALLMENT,
                            category=categorize_merchant(title),
                            total_amount=round(amount * total, 2),
                            installment_count=total,
                            start_date=datetime.utcnow().date(),
                            source="upload",
                        )
                        db.add(plan)
                        db.flush()
                        for index in range(total):
                            db.add(
                                Installment(
                                    tenant_id=document.tenant_id,
                                    plan_id=plan.id,
                                    sequence=index + 1,
                                    due_date=(datetime.utcnow().date() + timedelta(days=30 * index)),
                                    amount=amount,
                                )
                            )
                    extracted_data["items"].append({"title": title, "installment": f"{current}/{total}", "amount": amount})

            if not extracted_data["items"]:
                extracted_data, confidence = extract_receipt_data(text, document.filename, document.document_type)
                summary = extracted_data.get("summary") or {}
                total_amount = summary.get("detected_total")
                merchant = summary.get("merchant") or f"Importacao {document.filename}"
                raw_date = summary.get("occurred_on")
                occurred_on = (parse_date_br(raw_date) if raw_date else None) or datetime.utcnow().date()
                if total_amount:
                    entry = FinancialEntry(
                        tenant_id=document.tenant_id,
                        user_id=document.user_id,
                        title=merchant[:160],
                        category="Cartao" if document.document_type == DocumentType.CREDIT_CARD else categorize_merchant(merchant),
                        entry_type=EntryType.EXPENSE,
                        amount=round(total_amount, 2),
                        occurred_on=occurred_on,
                        source="upload",
                        notes="Lancamento resumido gerado por extracao automatica",
                    )
                    db.add(entry)

        document.status = DocumentStatus.PROCESSED
        document.extracted_text = text[:15000]
        document.extracted_data = extracted_data
        document.confidence = confidence
        document.processed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        # Always rollback first: if the exception came from a DB operation the
        # session is in "pending rollback" state and a bare commit() would raise
        # PendingRollbackError, leaving the document stuck as PENDING forever.
        try:
            db.rollback()
        except Exception:
            pass
        try:
            fresh = db.get(Document, document_id)
            if fresh:
                fresh.status = DocumentStatus.FAILED
                fresh.extracted_data = {"error": str(exc)[:500]}
                fresh.processed_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
