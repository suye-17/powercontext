"""Construct small, independently readable PDF fixtures without layout libraries."""

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def make_pdf(
    *texts: str, password: str | None = None, title: str = "", corrupt_last: bool = False, content_array: bool = False
) -> bytes:
    writer = PdfWriter()
    for value in texts:
        page = writer.add_blank_page(width=600, height=800)
        if value:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
            })
            stream = DecodedStreamObject()
            escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 20 750 Td ({escaped}) Tj ET".encode("ascii"))
            reference = writer._add_object(stream)
            page[NameObject("/Contents")] = ArrayObject([reference]) if content_array else reference
        elif content_array:
            page[NameObject("/Contents")] = ArrayObject()
    if corrupt_last:
        invalid = NumberObject(7)
        writer.pages[-1][NameObject("/Contents")] = ArrayObject([invalid]) if content_array else invalid
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner")  # noqa: S106
    if title:
        writer.add_metadata({"/Title": title})
    result = BytesIO()
    writer.write(result)
    return result.getvalue()
