from __future__ import annotations

import base64
import json
import logging
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
# Also matches truncated amounts with only 1 decimal digit (OCR artifact: "31,7" instead of "31,72")
_AMOUNT_LOOSE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{1,2})")
# Matches weight/unit-price format: "0,4 KG x 79,30" or "2 UN x 15,00"
_QTY_UNIT_PRICE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:kg|un|g|l|ml|pc|pct|cx|lt)\s*x\s*(\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
# Tax approximation lines printed after each item — must never be used as line totals
_TAX_APPROX_RE = re.compile(r"aprox|imposto", re.IGNORECASE)
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
    if any(token in value for token in ["mercado", "super", "atac", "carrefour", "atacad", "hortifrut", "feira"]):
        return "Mercado"
    if any(token in value for token in ["padaria", "panificadora", "confeitaria", "lanchonete", "restaurante",
                                         "pizzaria", "hamburger", "burger", "cafe", "cafeteria", "sorveteria",
                                         "america", "pao", "doce", "salgado", "refeicao", "buffet"]):
        return "Alimentacao"
    if any(token in value for token in ["uber", "99", "posto", "combustivel", "gasolina", "shell", "ipiranga",
                                         "petrobras", "estacionamento", "parking", "onibus", "metro"]):
        return "Transporte"
    if any(token in value for token in ["netflix", "spotify", "cinema", "show", "ingresso", "teatro",
                                         "amazon prime", "disney", "hbo", "youtube"]):
        return "Lazer"
    if any(token in value for token in ["farm", "drog", "saude", "clinica", "hospital", "medico",
                                         "odonto", "laboratorio", "exame"]):
        return "Saude"
    if any(token in value for token in ["escola", "faculdade", "curso", "universidade", "colegio"]):
        return "Educacao"
    return "Outros"


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


def _parse_any_date(value: str | None) -> date | None:
    """Try ISO (YYYY-MM-DD) then BR (DD/MM/YYYY) format."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
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


_RECEIPT_ITEM_NOISE = re.compile(
    r"""
    ^\d{1,3}\s+          # leading sequence number: "001 ", "02 "
    | \b\d{8,}\b         # barcode / long numeric codes
    | \s+x\s+[\d,]+$     # trailing multiplier " x 31,7"
    | \s+un\b            # unit suffix
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _clean_receipt_label(raw: str) -> str:
    label = AMOUNT_PATTERN.sub("", raw)         # remove currency amounts
    label = _RECEIPT_ITEM_NOISE.sub(" ", label) # remove noise patterns
    label = re.sub(r"\s+", " ", label).strip(" -:%.")
    return label[:120]


def _parse_br_float(raw: str) -> float:
    """Parse a Brazilian-formatted number (e.g. '31,72' or '1.234,56') to float."""
    return float(raw.replace(".", "").replace(",", "."))


def extract_receipt_items(lines: list[str]) -> list[dict]:
    """Extract line items from a Brazilian fiscal receipt.

    Handles weight-based items where the receipt prints:
        001 843 BISCOITO DE NATA  0,4 KG x 79,30   ← unit price per kg
                                        31,72        ← actual line total (next line)
                (Vir Aprox. Impostos R$ 8,07)        ← tax info — must be ignored

    Resolution priority for the line total:
      1. Amount after the unit price on the SAME line (handles OCR-merged lines)
      2. Standalone amount on the NEXT line, if not a tax/approximation line
      3. Arithmetic: qty × unit_price
    """
    items: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        normalized = normalize_text(line)
        if "valor total" in normalized or "forma de pagamento" in normalized:
            break

        # Skip tax approximation lines entirely
        if _TAX_APPROX_RE.search(line):
            i += 1
            continue

        amount = extract_amount_from_line(line)
        if amount is None:
            i += 1
            continue
        # Must contain a 3-digit-or-longer code (product/barcode) to be a product line
        if not re.search(r"\b\d{3,}\b", line):
            i += 1
            continue

        # Detect weight/unit-price pattern: "0,4 KG x 79,30"
        qty_match = _QTY_UNIT_PRICE_RE.search(line)
        if qty_match:
            resolved = None

            # Strategy 1: arithmetic — qty × unit_price (mathematically exact;
            # preferred over OCR readings which can be truncated by column wrapping)
            try:
                qty = _parse_br_float(qty_match.group(1))
                unit_price = _parse_br_float(qty_match.group(2))
                resolved = round(qty * unit_price, 2)
            except (ValueError, IndexError):
                pass

            # Strategy 2: peek next non-empty, non-tax line for a standalone amount
            if resolved is None:
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    next_line = lines[j]
                    next_amounts = AMOUNT_PATTERN.findall(next_line)
                    if (
                        len(next_amounts) == 1
                        and not re.search(r"\b\d{3,}\b", next_line)
                        and not _TAX_APPROX_RE.search(next_line)
                    ):
                        resolved = _parse_br_float(next_amounts[0])
                        i = j  # consume the line-total line

            # Strategy 3: amount after the unit price on the same line (OCR may
            # merge columns; accept 1-decimal truncations like "31,7" → 31.70)
            if resolved is None:
                unit_price_end = qty_match.end()
                after_unit_price = line[unit_price_end:]
                loose_matches = _AMOUNT_LOOSE.findall(after_unit_price)
                if loose_matches:
                    raw = loose_matches[0]
                    if re.match(r"^\d{1,3}(?:\.\d{3})*,\d$", raw):
                        raw = raw + "0"
                    try:
                        resolved = _parse_br_float(raw)
                    except ValueError:
                        pass

            if resolved is not None:
                amount = resolved

        label = _clean_receipt_label(line)
        if len(label) < 4:
            i += 1
            continue
        items.append({"label": label, "amount": amount})
        i += 1
    return items[:10]


_GROQ_UNIFIED_PROMPT = """\
Analise esta imagem de documento financeiro brasileiro.
Primeiro identifique o tipo, depois extraia os dados. Retorne SOMENTE um JSON válido, sem markdown, sem explicações.

Se for HOLERITE / CONTRACHEQUE / FOLHA DE PAGAMENTO:
{
  "document_type": "payslip",
  "employee_name": "nome completo ou null",
  "company_name": "nome da empresa ou null",
  "competence": "MM/YYYY ou null",
  "gross_income": 0.00,
  "net_income": 0.00,
  "discounts": 0.00,
  "inss": 0.00,
  "irrf": 0.00,
  "vt": 0.00,
  "vr": 0.00
}

Se for NOTA FISCAL / CUPOM / COMPROVANTE DE COMPRA / RECEIPT:
{
  "document_type": "receipt",
  "merchant": "nome do estabelecimento",
  "date": "YYYY-MM-DD ou null",
  "items": [{"label": "nome do produto", "amount": 0.00}],
  "total": 0.00
}

Se for FATURA DE CARTÃO DE CRÉDITO:
{
  "document_type": "credit_card",
  "merchant": "nome da operadora ou null",
  "date": "YYYY-MM-DD ou null",
  "items": [{"label": "descricao do lancamento", "amount": 0.00}],
  "total": 0.00
}

Regras gerais:
- Valores numéricos sempre em float (ex: 31,72 → 31.72).
- Para itens por peso (KG/g): use valor total pago (qtd × preço unitário), não o preço por kg.
- Ignore linhas de impostos aproximados (Vir Aprox. Impostos).
- Campos não encontrados: use null (nunca omita a chave).
- Para holerite, campos de valor não encontrados: use null (nunca 0.00).
"""

_GROQ_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_GROQ_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

# Keywords for text-based document type detection (used for PDFs/TXT where vision is unavailable)
_PAYSLIP_KEYWORDS = [
    "contracheque", "holerite", "folha de pagamento", "folha salarial",
    "salario base", "salario bruto", "liquido a receber", "total de vencimentos",
    "total de descontos", "inss", "irrf", "rubrica", "competencia",
]
_RECEIPT_KEYWORDS = [
    "nota fiscal", "nfc-e", "nf-e", "cupom fiscal", "documento auxiliar",
    "cnpj do emitente", "valor total r$", "forma de pagamento", "troco",
]
_CREDIT_CARD_KEYWORDS = [
    "fatura", "cartao de credito", "vencimento da fatura", "limite disponivel",
    "pagamento minimo", "valor minimo", "limite de credito",
]


def _detect_type_from_text(filename: str, text: str) -> DocumentType | None:
    """Infer document type from filename and extracted text (fallback for PDFs/TXT).

    Returns None if classification is uncertain (caller keeps original type).
    """
    name_lower = normalize_text(filename)
    text_lower = normalize_text(text[:2000])  # first 2000 chars are enough

    # Filename is a strong signal
    if any(kw in name_lower for kw in ["contracheque", "holerite", "folha", "payslip", "payroll"]):
        return DocumentType.PAYSLIP
    if any(kw in name_lower for kw in ["fatura", "cartao", "credit"]):
        return DocumentType.CREDIT_CARD
    if any(kw in name_lower for kw in ["nota", "nfce", "cupom", "recibo", "receipt"]):
        return DocumentType.RECEIPT

    # Count keyword hits in text
    payslip_hits = sum(1 for kw in _PAYSLIP_KEYWORDS if kw in text_lower)
    receipt_hits = sum(1 for kw in _RECEIPT_KEYWORDS if kw in text_lower)
    credit_hits = sum(1 for kw in _CREDIT_CARD_KEYWORDS if kw in text_lower)

    best = max(payslip_hits, receipt_hits, credit_hits)
    if best < 2:  # not confident enough
        return None
    if payslip_hits == best:
        return DocumentType.PAYSLIP
    if credit_hits == best:
        return DocumentType.CREDIT_CARD
    return DocumentType.RECEIPT


def _analyze_image_with_groq(image_path: Path, filename: str) -> tuple[DocumentType, dict, float] | None:
    """Use Llama Vision via Groq to detect document type AND extract data in one call.

    Returns (detected_type, extracted_data, confidence) or None on failure/no key.
    The caller should update document.document_type with detected_type.
    """
    from app.config import get_settings
    api_key = get_settings().groq_api_key
    if not api_key:
        return None

    suffix = image_path.suffix.lower()
    if suffix not in _GROQ_IMAGE_SUFFIXES:
        return None

    media_type = _GROQ_MEDIA_TYPES.get(suffix, "image/jpeg")

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        image_data = base64.standard_b64encode(image_path.read_bytes()).decode()

        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
                    {"type": "text", "text": _GROQ_UNIFIED_PROMPT},
                ],
            }],
            max_tokens=1024,
            temperature=0.1,
        )

        raw = response.choices[0].message.content or ""
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        data = json.loads(raw)

        doc_type_str = (data.get("document_type") or "receipt").lower()
        if doc_type_str == "payslip":
            detected_type = DocumentType.PAYSLIP
        elif doc_type_str == "credit_card":
            detected_type = DocumentType.CREDIT_CARD
        else:
            detected_type = DocumentType.RECEIPT

        if detected_type == DocumentType.PAYSLIP:
            def _f(key: str) -> float | None:
                v = data.get(key)
                return float(v) if v is not None else None

            summary = {
                "employee_name": data.get("employee_name"),
                "company_name": data.get("company_name"),
                "competence": data.get("competence"),
                "gross_income": _f("gross_income"),
                "net_income": _f("net_income"),
                "discounts": _f("discounts"),
                "inss": _f("inss"),
                "irrf": _f("irrf"),
                "vt": _f("vt"),
                "vr": _f("vr"),
            }
            filled = sum(1 for k in ("gross_income", "net_income", "discounts", "inss", "irrf") if summary[k] is not None)
            confidence = min(0.50 + filled * 0.09, 0.95)
            extracted_data = {
                "document_kind": "payslip",
                "filename": filename,
                "summary": summary,
                "items": [],
                "extracted_by": "groq-llama-vision",
            }
        else:
            items = [
                {"label": str(it.get("label", ""))[:120], "amount": float(it["amount"])}
                for it in (data.get("items") or [])
                if it.get("label") and it.get("amount") is not None
            ]
            total = data.get("total")
            if total is None and items:
                total = round(sum(i["amount"] for i in items), 2)
            occurred_on = _parse_any_date(data.get("date"))
            summary = {
                "merchant": (data.get("merchant") or filename)[:160],
                "detected_total": float(total) if total is not None else None,
                "occurred_on": occurred_on.isoformat() if occurred_on else None,
                "document_kind": doc_type_str,
                "extracted_by": "groq-llama-vision",
            }
            confidence = 0.92 if total is not None else 0.65
            extracted_data = {"summary": summary, "items": items}

        return detected_type, extracted_data, confidence

    except Exception as exc:
        logging.warning("Groq vision analysis failed, falling back to Tesseract: %s", exc)
        return None


def _analyze_text_with_groq(text: str, filename: str) -> tuple[DocumentType, dict, float] | None:
    """Send extracted text to Groq Llama (text model) for type detection + extraction.

    Used for PDFs and TXT files where vision is not available.
    Returns (detected_type, extracted_data, confidence) or None on failure/no key.
    """
    from app.config import get_settings
    api_key = get_settings().groq_api_key
    if not api_key:
        return None

    # Truncate to fit context — first 3000 chars cover most documents
    text_snippet = text[:3000].strip()
    if not text_snippet:
        return None

    prompt = f"""\
Analise o texto abaixo extraído de um documento financeiro brasileiro.
Identifique o tipo do documento e extraia os dados. Retorne SOMENTE um JSON válido, sem markdown.

{_GROQ_UNIFIED_PROMPT}

TEXTO DO DOCUMENTO:
\"\"\"
{text_snippet}
\"\"\"
"""

    try:
        from groq import Groq
        client = Groq(api_key=api_key)

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.1,
        )

        raw = response.choices[0].message.content or ""
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        data = json.loads(raw)

        # Reuse the same parsing logic as the vision path
        doc_type_str = (data.get("document_type") or "receipt").lower()
        if doc_type_str == "payslip":
            detected_type = DocumentType.PAYSLIP
        elif doc_type_str == "credit_card":
            detected_type = DocumentType.CREDIT_CARD
        else:
            detected_type = DocumentType.RECEIPT

        if detected_type == DocumentType.PAYSLIP:
            def _f(key: str) -> float | None:
                v = data.get(key)
                return float(v) if v is not None else None

            summary = {
                "employee_name": data.get("employee_name"),
                "company_name": data.get("company_name"),
                "competence": data.get("competence"),
                "gross_income": _f("gross_income"),
                "net_income": _f("net_income"),
                "discounts": _f("discounts"),
                "inss": _f("inss"),
                "irrf": _f("irrf"),
                "vt": _f("vt"),
                "vr": _f("vr"),
            }
            filled = sum(1 for k in ("gross_income", "net_income", "discounts", "inss", "irrf") if summary[k] is not None)
            confidence = min(0.50 + filled * 0.09, 0.95)
            extracted_data = {
                "document_kind": "payslip",
                "filename": filename,
                "summary": summary,
                "items": [],
                "extracted_by": "groq-llama-text",
            }
        else:
            items = [
                {"label": str(it.get("label", ""))[:120], "amount": float(it["amount"])}
                for it in (data.get("items") or [])
                if it.get("label") and it.get("amount") is not None
            ]
            total = data.get("total")
            if total is None and items:
                total = round(sum(i["amount"] for i in items), 2)
            occurred_on = _parse_any_date(data.get("date"))
            summary = {
                "merchant": (data.get("merchant") or filename)[:160],
                "detected_total": float(total) if total is not None else None,
                "occurred_on": occurred_on.isoformat() if occurred_on else None,
                "document_kind": doc_type_str,
                "extracted_by": "groq-llama-text",
            }
            confidence = 0.92 if total is not None else 0.65
            extracted_data = {"summary": summary, "items": items}

        return detected_type, extracted_data, confidence

    except Exception as exc:
        logging.warning("Groq text analysis failed, falling back to Tesseract: %s", exc)
        return None


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


def cleanup_generated_document_entries(db: Session, document: Document) -> None:
    db.execute(
        delete(FinancialEntry).where(
            FinancialEntry.tenant_id == document.tenant_id,
            FinancialEntry.user_id == document.user_id,
            FinancialEntry.source == "upload",
            FinancialEntry.notes.like(f"%document_id={document.id}%"),
        )
    )


def _create_entries_from_result(
    db: Session,
    document: Document,
    extracted_data: dict,
    doc_type: DocumentType,
) -> None:
    """Create one FinancialEntry per extracted item (receipt / credit card).

    Falls back to a single summary entry using detected_total when there are
    no individual items (e.g. Tesseract extracted only the total).
    """
    summary = extracted_data.get("summary") or {}
    items = extracted_data.get("items") or []
    occurred_on = _parse_any_date(summary.get("occurred_on")) or datetime.utcnow().date()
    merchant = (summary.get("merchant") or document.filename)[:160]
    is_credit_card = doc_type == DocumentType.CREDIT_CARD

    if items:
        # Use merchant-level category as fallback when item name alone is ambiguous
        merchant_category = "Cartao" if is_credit_card else categorize_merchant(merchant)
        for item in items:
            label = (item.get("label") or "").strip()
            amount = item.get("amount")
            if not label or not amount or float(amount) <= 0:
                continue
            item_category = categorize_merchant(label) if not is_credit_card else "Cartao"
            category = item_category if item_category != "Outros" else merchant_category
            db.add(FinancialEntry(
                tenant_id=document.tenant_id,
                user_id=document.user_id,
                title=label[:160],
                category=category,
                entry_type=EntryType.EXPENSE,
                amount=round(float(amount), 2),
                occurred_on=occurred_on,
                source="upload",
                notes=f"Importado de {merchant} (document_id={document.id})",
            ))
    else:
        # No items — fall back to single total entry
        total_amount = summary.get("detected_total")
        if total_amount:
            db.add(FinancialEntry(
                tenant_id=document.tenant_id,
                user_id=document.user_id,
                title=merchant,
                category="Cartao" if is_credit_card else categorize_merchant(merchant),
                entry_type=EntryType.EXPENSE,
                amount=round(float(total_amount), 2),
                occurred_on=occurred_on,
                source="upload",
                notes=f"Total importado automaticamente (document_id={document.id})",
            ))


def sync_spending_outputs(db: Session, document: Document, extracted_data: dict) -> None:
    cleanup_generated_document_entries(db, document)
    _create_entries_from_result(db, document, extracted_data, document.document_type)


def process_document(db: Session, document_id: int) -> None:
    document = db.get(Document, document_id)
    if not document:
        return

    try:
        stored_path = Path(document.stored_path)
        text = extract_text_from_file(stored_path)
        extracted_data: dict = {"summary": {}, "items": []}
        confidence = 0.45

        # ── LLM analysis: every upload goes through Groq ──────────────────────
        # Images → Llama Vision;  PDFs/TXT → Llama text model (extracted text).
        # Falls back to Tesseract-only pipeline when GROQ_API_KEY is not set.
        if stored_path.suffix.lower() in _GROQ_IMAGE_SUFFIXES:
            groq_result = _analyze_image_with_groq(stored_path, document.filename)
        else:
            groq_result = _analyze_text_with_groq(text, document.filename)

        if groq_result is not None:
            detected_type, extracted_data, confidence = groq_result
            if detected_type != document.document_type:
                logging.info(
                    "document %s: user selected %s but LLM detected %s — overriding",
                    document_id, document.document_type, detected_type,
                )
                document.document_type = detected_type

            if detected_type == DocumentType.PAYSLIP:
                sync_payslip_outputs(db, document, extracted_data)

        # ── Tesseract-only fallback (GROQ_API_KEY not set or Groq call failed) ─
        else:
            detected = _detect_type_from_text(document.filename, text)
            if detected is not None and detected != document.document_type:
                document.document_type = detected

            if document.document_type == DocumentType.PAYSLIP:
                extracted_data, confidence = extract_payslip_data(text, document.filename)
                sync_payslip_outputs(db, document, extracted_data)

            else:
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
