"""
Locale Parser Utility for Spanish Utility Invoices.
Handles Spanish number formatting ('1.234,56 €' -> Decimal('1234.56')),
currency strings, date representations, and formatted output.
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, date
import re
from typing import Optional, Union

SPANISH_MONTHS = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
}


def parse_spanish_decimal(val: Union[str, int, float, Decimal, None]) -> Optional[Decimal]:
    """
    Parses a Spanish numeric string into a Python Decimal.
    Examples:
      '1.234,56 €'      -> Decimal('1234.56')
      '-1.234,56'       -> Decimal('-1234.56')
      '0,082245 €/kW'   -> Decimal('0.082245')
      '  45,00 EUR '    -> Decimal('45.00')
      '1234.56'         -> Decimal('1234.56')
    """
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))

    s = str(val).strip()
    if not s or s.lower() in ('null', 'none', '-', 'n/a'):
        return None

    # Remove currency signs, unit labels, spaces
    # Keep digits, dot, comma, minus sign
    is_negative = '-' in s
    s_clean = re.sub(r'[^\d.,]', '', s)
    if not s_clean:
        return None

    # Determine if comma or dot is the decimal separator
    if ',' in s_clean and '.' in s_clean:
        # Standard Spanish '1.234,56': dot is thousand, comma is decimal
        if s_clean.rfind(',') > s_clean.rfind('.'):
            s_clean = s_clean.replace('.', '').replace(',', '.')
        else:
            # Anglo format '1,234.56'
            s_clean = s_clean.replace(',', '')
    elif ',' in s_clean:
        # Only comma present: '1234,56' -> '1234.56'
        s_clean = s_clean.replace(',', '.')
    elif '.' in s_clean:
        # Only dot present: could be '1234.56' or thousand separator '1.234'
        # In Spanish bills, if 3 digits follow dot and no comma, it might be thousands,
        # but for tariff prices like 0.082245 or amounts, standard decimal dot is common in IT.
        # If multiple dots, they are thousand separators
        if s_clean.count('.') > 1:
            s_clean = s_clean.replace('.', '')
        else:
            # Single dot: keep as is
            pass

    try:
        dec = Decimal(s_clean)
        return -dec if is_negative else dec
    except (InvalidOperation, ValueError):
        return None


def format_spanish_currency(val: Union[Decimal, float, int, None], decimals: int = 2, show_symbol: bool = True) -> str:
    """
    Formats a numeric value to Spanish currency standard: '1.234,56 €'.
    """
    if val is None:
        return "0,00 €" if show_symbol else "0,00"

    try:
        if isinstance(val, float):
            d = Decimal(str(round(val, decimals)))
        elif isinstance(val, int):
            d = Decimal(val)
        else:
            d = val
        
        # Round half up
        quantize_str = '0.' + ('0' * decimals) if decimals > 0 else '0'
        d_rounded = d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
        sign = "-" if d_rounded < 0 else ""
        abs_d = abs(d_rounded)
        
        parts = f"{abs_d:.{decimals}f}".split('.')
        integer_part = parts[0]
        decimal_part = parts[1] if len(parts) > 1 else ""

        # Insert thousands separator
        formatted_int = ""
        for i, ch in enumerate(reversed(integer_part)):
            if i > 0 and i % 3 == 0:
                formatted_int = "." + formatted_int
            formatted_int = ch + formatted_int

        if decimals > 0:
            result = f"{sign}{formatted_int},{decimal_part}"
        else:
            result = f"{sign}{formatted_int}"

        if show_symbol:
            return f"{result} €"
        return result
    except Exception:
        return f"{val} €" if show_symbol else str(val)


def parse_spanish_date(val: Union[str, date, datetime, None]) -> Optional[date]:
    """
    Parses various Spanish date formats into datetime.date:
      - '15/11/2026'
      - '15-11-2026'
      - '15.11.2026'
      - '2026-11-15'
      - '15 de noviembre de 2026'
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val

    s = str(val).strip()
    if not s:
        return None

    # Check for text format: '15 de noviembre de 2026'
    text_match = re.search(r'(\d{1,2})\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚ]+)\s+de\s+(\d{4})', s, re.IGNORECASE)
    if text_match:
        d_str, month_str, y_str = text_match.groups()
        month_lower = month_str.lower()
        if month_lower in SPANISH_MONTHS:
            try:
                return date(int(y_str), SPANISH_MONTHS[month_lower], int(d_str))
            except ValueError:
                pass

    # Standard date patterns
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d', '%Y/%m/%d', '%d/%m/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # Try regex search within string (e.g. 'Fecha: 15/11/2026')
    date_regex = re.search(r'\b(\d{2})[\/\.-](\d{2})[\/\.-](\d{4})\b', s)
    if date_regex:
        day, month, year = date_regex.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            pass

    return None
