# Derindex: Gelecek Yol Haritası (Roadmap)

Bu belge, **Derindex** projesinin temel prensiplerini ve gelecekte sisteme kazandırılması planlanan mimari, algoritmik ve kullanıcı deneyimi geliştirmelerini içerir.

---

## 🧭 Temel Tasarım İlkeleri (Core Philosophy)

1. **Sıfır LLM Kuralı (No Generative LLMs):**
   Derindex, devasa yapay zeka modelleriyle halüsinasyon üreten veya gigabaytlarca RAM tüketen bir sohbet botu **değildir ve asla olmayacaktır**. Sistem; deterministik, matematiksel ve ultra-hızlı bir **Bilgi Erişimi (Information Retrieval - IR)** ve **Gömme Tabanlı Semantik Arama (Dense Vector Retrieval)** motorudur.
2. **%100 Yerel ve Sıfır Bulut Bağımlılığı (Offline-First):**
   Hiçbir veri, sorgu veya telemetri dış ağa gönderilmez. Tüm indeksleme ve embedding yerel CPU/GPU üzerinde gerçekleşir.
3. **Milisaniye Seviyesinde Yanıt Süresi:**
   Tüm mimari kararlar (inverted index, BM25, NumPy/HNSW, SQLite) anlık arama deneyimini koruyacak şekilde optimize edilir.

---

## 🗺️ Geliştirme Yol Haritası

```
                               DERINDEX ROADMAP
                                      │
     ┌──────────────────┬─────────────┴──────────────┬──────────────────┐
     ▼                  ▼                            ▼                  ▼
  [FAZ 1]            [FAZ 2]                      [FAZ 3]            [FAZ 4]
 IR Derinliği &     Milyon Doküman &             Desktop & IDE      Zengin Medya
 Neural Rerank       HNSW Vektör                  Entegrasyonu        & STT Ses
```

---

### Faz 1: Arama Kalitesi ve IR Derinliği (State-of-the-Art Precision)

- [ ] **Hafif Cross-Encoder Yeniden Sıralama (Neural Reranker):**
  - BM25 ve Bi-Encoder vektör araması ile ilk 20-30 aday seçildikten sonra, hafif bir Cross-Encoder modeli (örn: `bge-reranker-base` veya `ms-marco-MiniLM-L-6-v2`) ile sorgu ve metin çiftleri ultra-hassas bir şekilde yeniden puanlanacak.
  - Bu işlem jeneratif LLM gerektirmez; yalnızca ikili alaka skoru (relevance logit) üreten küçük bir sınıflandırıcıdır.
- [ ] **Eş Anlamlı ve Kısaltma Genişletmesi (Synonym & Acronym Expansion):**
  - Teknik terimler ve yaygın kısaltmalar için yerel eşanlamlılar tablosu (örn: `k8s` $\leftrightarrow$ `kubernetes`, `db` $\leftrightarrow$ `database`, `auth` $\leftrightarrow$ `authentication`).
- [ ] **Çok Dilli Stemmer / Lemmatizer Seçeneği:**
  - Kod terimlerini bozmadan doğal dil dökümanlarında morfolojik varyasyonları eşleştiren opsiyonel kök bulucu desteği.

---

### Faz 2: Büyük Veri ve Vektör Ölçeklenebilirliği (Extreme Scalability)

- [ ] **HNSW / USearch Graf Vektör İndeksi:**
  - Mevcut NumPy matris çarpımı 50.000 chunk'a kadar milisaniyeler içinde çalışmaktadır.
  - 500.000+ chunk ve milyon döküman seviyesindeki büyük arşivler için bellek-haritalı (memory-mapped) HNSW graf indeksi (`usearch` veya `faiss-cpu`) entegre edilecek.
- [ ] **Dinamik LRU Sorgu Önbelleği (Query Cache):**
  - Sık yinelenen sorguların BM25 ve embedding hesaplamalarını RAM'de saklayan thread-safe LRU önbellek mekanizması.
- [ ] **Arka Plan İndeksleme Sıkıştırması (Vacuum & Optimize Cron):**
  - Silinen ve güncellenen dosyalardan sonra SQLite veritabanını ve vektör deposunu boş zamanlarda optimize eden otomatik bakım iş parçacığı.

---

### Faz 3: Arayüz ve Sistem Entegrasyonu (Desktop & Developer UX)

- [ ] **Tarayıcı İçi Döküman Önizleme Modalı (In-Browser Document Preview):**
  - Arama sonucuna tıklandığında dosyayı harici bir uygulamayla açmak yerine, doğrudan arayüz içinde PDF sayfasını veya kod bloğunu satır vurgulamasıyla (syntax highlighting) açan modal önizleme paneli.
- [ ] **Sistem Geneli Kısayol Uygulaması (Spotlight / Raycast Launcher):**
  - Rust ve Tauri ile geliştirilecek 10 MB'lık ultra-hafif masaüstü uygulaması.
  - `Alt + Space` veya `Ctrl + Shift + F` ile işletim sisteminin herhangi bir yerinde açılan anlık arama çubuğu.
- [ ] **VS Code / Cursor IDE Eklentisi:**
  - Yazılımcıların kod yazarken editörden ayrılmadan yerel kod tabanında ve dökümanlarında Derindex API'si üzerinden arama yapabilmesi.

---

### Faz 4: Zengin Ayrıştırıcılar ve Yerel Ses Transkripsiyonu (Local STT)

- [ ] **Yerel Whisper / Vosk ile Ses Transkripsiyonu (Speech-to-Text):**
  - Ses ve video dosyalarındaki konuşmaları tamamen çevrimdışı küçük bir modelle (`whisper.cpp` veya `vosk`) metne döküp indeksleme desteği.
- [ ] **Jupyter Notebook (`.ipynb`) Zengin Hücre Ayrıştırma:**
  - Kod hücreleri, markdown açıklamaları ve metin çıktılarını ayrı ayrı etiketleyerek indeksleme.
- [ ] **Git Repository Metadata Entegrasyonu:**
  - Git reposu olan klasörlerde son commit mesajı, yazar ve branch bilgilerini döküman metadatasına ekleyerek `author:` veya `branch:` filtreleri sunma.
