import sys
from typing import Dict, Optional

class MemoryManager:
    """Simulates a virtual memory allocation and page frame manager."""
    def __init__(self, total_pages: int = 1024):
        self.total_pages = total_pages
        self.allocated_pages: Dict[int, str] = {}

    def allocate_page(self, process_id: int) -> int:
        """Allocates a new virtual memory page frame to a process."""
        page_id = len(self.allocated_pages) + 1
        self.allocated_pages[page_id] = f"proc_{process_id}"
        return page_id

    def free_page(self, page_id: int) -> bool:
        """Releases the memory page back to the operating system pool."""
        if page_id in self.allocated_pages:
            del self.allocated_pages[page_id]
            return True
        return False
