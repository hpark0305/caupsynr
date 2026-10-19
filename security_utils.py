"""Validation at file and spreadsheet trust boundaries."""
import csv
import re
import unicodedata
import zipfile
import warnings

from PIL import Image

DOCUMENT_EXTENSIONS = {'.pdf', '.txt', '.csv', '.tsv', '.json', '.md', '.xlsx', '.xls',
                       '.docx', '.pptx', '.hwp', '.hwpx', '.png', '.jpg', '.jpeg', '.webp'}


def password_policy_error(password, email=''):
    """Reject obvious weak new passwords without changing existing login rules."""
    if not 12 <= len(password) <= 128:
        return '비밀번호는 12~128자로 입력해주세요.'
    normalized = unicodedata.normalize('NFKC', password).casefold()
    if any(unicodedata.category(c).startswith('C') for c in password):
        return '비밀번호에는 제어 문자를 사용할 수 없어요.'
    compact = ''.join(c for c in normalized if c.isalnum())
    if len(set(compact)) < 4 or re.fullmatch(r'(.{1,4})\1+', compact):
        return '같은 문자나 짧은 패턴을 반복한 비밀번호는 사용할 수 없어요.'
    # A small local denylist is not a comprehensive breached-password check.
    weak = ('password', 'qwerty', '123456', 'abcdef', 'letmein', 'changeme', '비밀번호')
    if any(word in compact for word in weak):
        return '쉽게 추측할 수 있는 단어·연속 문자열 대신 긴 문구를 사용해주세요.'
    account = ''.join(c for c in email.partition('@')[0].casefold() if c.isalnum())
    if len(account) >= 4 and account in compact:
        return '이메일 아이디가 포함되지 않은 비밀번호를 사용해주세요.'
    return None


def validate_production_settings(environ):
    """Never fall back to a bundled local account database on Render."""
    if environ.get('RENDER', '').lower() != 'true':
        return
    if not environ.get('SUPABASE_URL') or not environ.get('SUPABASE_KEY'):
        raise SystemExit('운영 환경에서는 SUPABASE_URL과 서버 전용 SUPABASE_KEY가 필수입니다.')
    if environ.get('SESSION_COOKIE_SECURE', 'true').lower() == 'false':
        raise SystemExit('운영 환경에서는 Secure 세션 쿠키를 끌 수 없습니다.')


def validate_archive(stream):
    """Check expansion budget before a document parser opens an OOXML ZIP."""
    try:
        with zipfile.ZipFile(stream) as archive:
            files = archive.infolist()
            if len(files) > 2000 or sum(f.file_size for f in files) > 100 * 1024 * 1024:
                raise ValueError('Archive too large')
            if any(f.file_size > max(f.compress_size, 1) * 200 for f in files):
                raise ValueError('Archive compression ratio too high')
    finally:
        stream.seek(0)


def validate_image(stream, extension):
    formats = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.gif': 'GIF',
               '.webp': 'WEBP', '.avif': 'AVIF'}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(stream) as image:
                if image.format != formats.get(extension) or image.width * image.height > 20_000_000:
                    raise ValueError('Invalid image')
                image.verify()
    finally:
        stream.seek(0)


def spreadsheet_text(value):
    """Neutralize formulas in CSV downloads; retain numbers as numbers."""
    if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@'))
                                   or value.startswith(('\t', '\r', '\n'))):
        return "'" + value
    return value


class SafeCSVWriter:
    def __init__(self, stream):
        self.writer = csv.writer(stream)

    def writerow(self, row):
        return self.writer.writerow([spreadsheet_text(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def neutralize_excel_formulas(workbook):
    # Exports contain data, never intentional Excel formulas.
    for sheet in workbook:
        for row in sheet:
            for cell in row:
                if cell.data_type == 'f':
                    cell.data_type = 's'
