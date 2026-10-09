from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: int
    filename: str
    media_type: str
    size_bytes: int
    width: int
    height: int
    status: str
    preview_url: str
