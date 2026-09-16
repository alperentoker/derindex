#include <iostream>
#include <vector>

struct PageTableEntry {
    uint32_t frame_number;
    bool is_present;
    bool is_dirty;
};

class KernelMemoryPool {
public:
    KernelMemoryPool(size_t pool_size) : size_(pool_size) {}

    void* allocate_raw_block(size_t bytes) {
        std::cout << "Allocating kernel memory block of size: " << bytes << std::endl;
        return malloc(bytes);
    }

private:
    size_t size_;
};
