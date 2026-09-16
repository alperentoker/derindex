# Linux Bellek Yönetimi ve Sanal Bellek (Virtual Memory)

## Virtual Memory Mimarisi
Linux işletim sisteminde sanal bellek (virtual memory), fiziksel RAM'in sınırlarını aşmak ve her sürece (process) bağımsız bir 64-bit adres alanı sunmak amacıyla kullanılır.

### Sayfalama (Paging) ve Page Tables
İşlemcinin MMU (Memory Management Unit) birimi, sanal adresleri fiziksel adreslere dönüştürür.
Bellek genellikle 4 KB'lık sayfalara (pages) bölünür.
TLB (Translation Lookaside Buffer), son çevrilen adresleri önbelleğe alarak bellek erişimini hızlandırır.

### Swapping ve Page Fault
Fiziksel RAM dolduğunda, az kullanılan sayfalar diske (swap alanına) yazılır.
Bir sayfa bellekte bulunamadığında işletim sistemi bir Page Fault (Sayfa Hatası) kesmesi üretir ve sayfayı diskten RAM'e yükler.
