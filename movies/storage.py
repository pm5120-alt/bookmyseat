from django.conf import settings
from django.core.files.storage import FileSystemStorage

class PrivateTicketStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)

private_ticket_storage = PrivateTicketStorage()
