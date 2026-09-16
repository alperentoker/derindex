// Derindex Interactive Web Client (Pure Search & Semantic Retrieval)

let currentMode = 'hybrid';
let currentAlpha = 0.5;

document.addEventListener('DOMContentLoaded', () => {
  loadSystemStats();

  const searchInput = document.getElementById('search-input');
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      performSearch();
    }
  });

  // Global shortcut: press '/' to focus search input (only when not in an input/textarea)
  document.addEventListener('keydown', (e) => {
    const isInputActive = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if (e.key === '/' && !isInputActive) {
      e.preventDefault();
      switchTab('search');
      searchInput.focus();
    }
  });
});

// TAB SWITCHING
function switchTab(tabId) {
  const tabs = ['search', 'status'];
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-btn-${t}`);
    const view = document.getElementById(`view-${t}`);
    if (t === tabId) {
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      view.style.display = 'block';
    } else {
      btn.classList.remove('active');
      btn.setAttribute('aria-selected', 'false');
      view.style.display = 'none';
    }
  });

  if (tabId === 'status') {
    loadSystemStats();
  }
}

// SEARCH ENGINE
function setSearchMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.segment-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`mode-${mode}`).classList.add('active');

  const sliderContainer = document.getElementById('alpha-slider-container');
  const slider = document.getElementById('alpha-slider');

  if (mode === 'bm25') {
    currentAlpha = 1.0;
    sliderContainer.style.opacity = '0.5';
    sliderContainer.style.pointerEvents = 'none';
    slider.value = 1.0;
    document.getElementById('alpha-val-badge').textContent = '1.00';
  } else if (mode === 'semantic') {
    currentAlpha = 0.0;
    sliderContainer.style.opacity = '0.5';
    sliderContainer.style.pointerEvents = 'none';
    slider.value = 0.0;
    document.getElementById('alpha-val-badge').textContent = '0.00';
  } else {
    currentAlpha = 0.5;
    sliderContainer.style.opacity = '1.0';
    sliderContainer.style.pointerEvents = 'auto';
    slider.value = 0.5;
    document.getElementById('alpha-val-badge').textContent = '0.50';
  }

  const query = document.getElementById('search-input').value.trim();
  if (query) {
    performSearch();
  }
}

function onAlphaChange(val) {
  currentAlpha = parseFloat(val);
  document.getElementById('alpha-val-badge').textContent = currentAlpha.toFixed(2);
}

function clearSearchInput() {
  const input = document.getElementById('search-input');
  input.value = '';
  input.focus();
  document.getElementById('results-list').innerHTML = '';
  document.getElementById('results-header').style.display = 'none';
  document.getElementById('results-empty').style.display = 'block';
}

async function performSearch() {
  const input = document.getElementById('search-input');
  const query = input.value.trim();
  if (!query) return;

  const codeOnly = document.getElementById('code-only-toggle').checked;
  const loading = document.getElementById('results-loading');
  const empty = document.getElementById('results-empty');
  const list = document.getElementById('results-list');
  const header = document.getElementById('results-header');

  loading.style.display = 'block';
  empty.style.display = 'none';
  header.style.display = 'none';
  list.innerHTML = '';

  try {
    const params = new URLSearchParams({
      q: query,
      alpha: currentAlpha.toString(),
      limit: '15',
      code_only: codeOnly.toString()
    });

    const res = await fetch(`/api/search?${params}`);
    const data = await res.json();

    loading.style.display = 'none';
    header.style.display = 'flex';
    document.getElementById('results-count').textContent = `${data.count} sonuç bulundu`;
    document.getElementById('results-meta').textContent = `Mod: ${currentMode.toUpperCase()} | Alpha: ${currentAlpha.toFixed(2)}`;

    if (data.results.length === 0) {
      empty.style.display = 'block';
      empty.querySelector('h3').textContent = `'${query}' için sonuç bulunamadı`;
      empty.querySelector('p').textContent = `Farklı anahtar kelimeler deneyin veya alfa ağırlığını değiştirin.`;
      return;
    }

    renderSearchResults(data.results, query);
  } catch (err) {
    loading.style.display = 'none';
    empty.style.display = 'block';
    empty.querySelector('h3').textContent = 'Arama sırasında bir hata oluştu';
    empty.querySelector('p').textContent = err.message;
  }
}

function renderSearchResults(results, query) {
  const list = document.getElementById('results-list');
  list.innerHTML = '';

  results.forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'result-card';

    const ext = item.filename.split('.').pop() || 'doc';
    const badgesHtml = [];

    if (item.section) {
      badgesHtml.push(`<span class="badge section">Section: ${escapeHtml(item.section)}</span>`);
    } else if (item.page_number) {
      badgesHtml.push(`<span class="badge page">Sayfa ${item.page_number}</span>`);
    }

    if (item.start_line && item.end_line) {
      badgesHtml.push(`<span class="badge lines">Satır ${item.start_line}-${item.end_line}</span>`);
    }

    if (item.symbol_name) {
      badgesHtml.push(`<span class="badge symbol">Sembol: ${escapeHtml(item.symbol_name)}</span>`);
    }

    card.innerHTML = `
      <div class="result-header">
        <div class="result-title-group">
          <div class="file-type-icon">${ext.toUpperCase().slice(0, 4)}</div>
          <div>
            <div class="result-filename">${escapeHtml(item.filename)}</div>
            <div class="result-path">${escapeHtml(item.path)}</div>
          </div>
        </div>
        <div class="result-score-pill">
          <span class="score-main">${item.score.toFixed(3)}</span>
          <span class="score-breakdown">BM25: ${item.bm25_score.toFixed(2)} | Sem: ${item.semantic_score.toFixed(2)}</span>
        </div>
      </div>
      ${badgesHtml.length > 0 ? `<div class="result-badges">${badgesHtml.join('')}</div>` : ''}
      <div class="result-snippet">${highlightQuery(item.matched_snippet, query)}</div>
    `;

    list.appendChild(card);
  });
}

function highlightQuery(text, query) {
  if (!text) return '';
  const words = query.trim().split(/\s+/).filter(w => w.length > 1);
  if (words.length === 0) return escapeHtml(text);

  const pattern = new RegExp(`(${words.map(escapeRegExp).join('|')})`, 'gi');
  return escapeHtml(text).replace(pattern, '<mark>$1</mark>');
}

// SYSTEM STATS & INDEXING
async function loadSystemStats() {
  try {
    const res = await fetch('/api/stats');
    const data = await res.json();

    document.getElementById('stat-docs').textContent = data.stats.total_documents.toLocaleString();
    document.getElementById('stat-chunks').textContent = data.stats.total_chunks.toLocaleString();
    document.getElementById('stat-terms').textContent = data.stats.total_unique_terms.toLocaleString();
    document.getElementById('stat-symbols').textContent = data.stats.total_symbols.toLocaleString();
  } catch (err) {
    console.warn('Could not fetch stats:', err);
  }
}

async function triggerFolderIndex() {
  const input = document.getElementById('index-folder-input');
  const path = input.value.trim();
  if (!path) return;

  const btn = document.getElementById('btn-trigger-index');
  const out = document.getElementById('index-output');

  btn.disabled = true;
  btn.textContent = 'İndeksleniyor...';
  out.style.display = 'block';
  out.className = 'index-output-msg';
  out.textContent = 'Klasör taranıyor ve indeks güncelleniyor...';

  try {
    const res = await fetch('/api/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path })
    });
    const data = await res.json();

    btn.disabled = false;
    btn.textContent = 'İndekslemeyi Başlat';

    if (res.ok) {
      out.className = 'index-output-msg success';
      out.textContent = `Başarılı! Taranan: ${data.stats.scanned}, İndekslenen: ${data.stats.indexed}, Atlanan: ${data.stats.skipped}`;
      loadSystemStats();
    } else {
      out.className = 'index-output-msg error';
      out.textContent = `Hata: ${data.detail || 'İndeksleme başarısız'}`;
    }
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'İndekslemeyi Başlat';
    out.className = 'index-output-msg error';
    out.textContent = `Hata: ${err.message}`;
  }
}

// HELPERS
function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeRegExp(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
