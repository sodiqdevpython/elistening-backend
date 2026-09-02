"""IELTS Listening test parser — engnovate.com dan tayyor standalone HTML.

Kod asosan `parser/scrape.py` dan olingan (loyihaning ildizidagi CLI skript).
Bu yerda funksiyalar sifatida qayta yozilgan: `parse_test(url) -> {html,
title, total_questions}`. Backend `IeltsListeningTest` bilan bog'lanadi.

Qo'llab-quvvatlaydigan savol turlari (parser HTML da hammasini saqlaydi;
frontend sandbox iframe orqali render qiladi):
    - fill-in-the-gap (text input)
    - single-choice (radio A/B/C)
    - multi-choice (checkbox, TWO letters, A-E)
    - drag & drop matching (dnd-card -> options-drop-zone)

Har savol input'iga `data-q` (savol raqami) qo'shiladi — sayt qismidagi
JavaScript (HTML ichida) 40 ta savolning javobini yig'ib `parent.postMessage`
orqali sayt sahifasiga jo'natadi.
"""
from __future__ import annotations

from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag


class IeltsParseError(Exception):
    """Parser ishlamaganda ko'tariladi (URL yetib bo'lmadi, sahifa noto'g'ri, ...)."""


# Sayt sahifasi (React) iframe ga sig'sin uchun template'ga POSTMESSAGE
# integration qo'shiladi: submit tugmasi va tozalash React'ga xabar beradi.
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  :root {{
    --bg:#f5f6f8; --card:#fff; --ink:#1a1f2b; --muted:#6a7180;
    --accent:#2b6cb0; --accent-2:#1a4a80; --border:#d9dde4;
    --input-border:#b8bfcb; --palette:#eef1f5;
    --palette-active:#2b6cb0; --palette-answered:#d1e9d1;
    --dnd-card:#fff3cd; --dnd-card-border:#f0c14b;
    --dnd-drop:#f0f4f8; --dnd-drop-border:#94a3b8;
    --dnd-drop-filled:#d1e9d1;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
         background:var(--bg); color:var(--ink); line-height:1.55; }}
  header {{ background:var(--card); border-bottom:1px solid var(--border);
            padding:14px 22px; position:sticky; top:0; z-index:20; }}
  header h1 {{ margin:0; font-size:18px; }}
  header .sub {{ color:var(--muted); font-size:13px; margin-top:2px; }}
  .tabs {{ display:flex; gap:6px; padding:12px 22px 0; background:var(--card);
           border-bottom:1px solid var(--border); }}
  .tab {{ padding:10px 18px; background:transparent; border:none;
          font-size:14px; font-weight:600; color:var(--muted); cursor:pointer;
          border-bottom:3px solid transparent; }}
  .tab.active {{ color:var(--accent); border-bottom-color:var(--accent); }}
  main {{ display:grid; grid-template-columns:1fr 260px; gap:20px;
          padding:20px; max-width:1400px; margin:0 auto; }}
  @media (max-width:900px) {{ main {{ grid-template-columns:1fr; }} }}
  .content {{ background:var(--card); border:1px solid var(--border);
              border-radius:10px; padding:24px; }}
  .content audio {{ width:100%; margin-bottom:20px; }}
  .part {{ display:none; }} .part.active {{ display:block; }}
  .part h2, .part h3 {{ font-size:18px; margin:22px 0 10px; }}
  .part > h2:first-of-type {{ font-size:20px; margin-top:0; }}
  table {{ border-collapse:collapse; width:100%; margin:12px 0; }}
  table td, table th {{ border:1px solid var(--border); padding:10px;
                        vertical-align:top; font-size:14px; }}
  input[type="text"] {{ background:#fff; border:1px solid var(--input-border);
                        border-radius:4px; padding:4px 8px; font-size:14px;
                        min-width:100px; margin:0 2px; font-family:inherit; }}
  input[type="text"]:focus {{ outline:none; border-color:var(--accent); }}
  .ielts-listening-question-number {{ display:inline-block; background:var(--accent);
                                       color:#fff; padding:1px 7px; border-radius:3px;
                                       font-size:12px; font-weight:700; margin-right:4px; }}
  .ielts-listening-question-item {{ display:block; margin:16px 0; padding:12px 14px;
                                     background:#fafbfc; border-left:3px solid var(--accent);
                                     border-radius:4px; }}
  table .ielts-listening-question-item, p .ielts-listening-question-item {{
    display:inline; margin:0; padding:0; background:transparent; border-left:none; }}
  .ielts-listening-option {{ display:flex; align-items:center; gap:8px; margin:6px 0 6px 24px; }}
  .ielts-listening-option input {{ margin:0; }}
  .ielts-listening-option-letter {{ display:inline-block; width:22px; height:22px;
                                     background:#eef1f5; border-radius:50%;
                                     text-align:center; line-height:22px;
                                     font-weight:700; font-size:12px; }}
  .ielts-listening-transcript-subhead {{ font-size:16px; font-weight:700; margin-top:18px; }}
  .matching-dnd-questions {{ margin: 12px 0; }}
  .matching-dnd-questions .ielts-listening-question-item {{
    display:flex; align-items:center; gap:12px; padding:10px 14px;
  }}
  .options-drop-zone {{
    display:inline-flex; align-items:center; gap:8px; min-width:180px;
    padding:8px 12px; border:2px dashed var(--dnd-drop-border);
    background:var(--dnd-drop); border-radius:6px; cursor:pointer;
    transition: background 0.15s, border-color 0.15s;
  }}
  .options-drop-zone.drag-over {{ border-color:var(--accent); background:#e0edf7; }}
  .options-drop-zone.filled {{ background:var(--dnd-drop-filled); border-style:solid; border-color:#5aa05a; }}
  .dnd-drop-placeholder {{ color:var(--muted); font-style:italic; font-size:13px; }}
  .dnd-drop-value {{ font-weight:600; }}
  .options-drop-zone.filled .dnd-drop-placeholder {{ display:none; }}
  .options-drop-zone .dnd-drop-value {{ display:none; }}
  .options-drop-zone.filled .dnd-drop-value {{ display:inline; }}
  .dnd-panel--matching {{
    margin: 16px 0; padding: 14px;
    background: #f9fafb; border: 1px solid var(--border); border-radius: 8px;
  }}
  .dnd-panel-instruction {{ font-size:13px; color:var(--muted); margin-bottom:10px; }}
  .dnd-cards-container {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .dnd-card {{
    display:inline-flex; align-items:center; gap:6px;
    padding:8px 12px; background:var(--dnd-card);
    border:1px solid var(--dnd-card-border); border-radius:6px;
    font-size:14px; cursor:grab; user-select:none;
  }}
  .dnd-card:active {{ cursor:grabbing; }}
  .dnd-card.used {{ opacity:0.35; cursor:not-allowed; }}
  .dnd-label {{ font-weight:700; color:#8a5a00; }}
  aside {{ position:sticky; top:92px; background:var(--card); border:1px solid var(--border);
           border-radius:10px; padding:16px; align-self:flex-start; height:fit-content; }}
  aside h3 {{ margin:0 0 10px; font-size:14px; color:var(--muted);
              text-transform:uppercase; letter-spacing:.5px; }}
  .palette-section {{ margin-bottom:14px; }}
  .palette-section strong {{ display:block; font-size:13px; margin-bottom:6px; }}
  .palette-grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:4px; }}
  .palette-item {{ aspect-ratio:1; display:flex; align-items:center; justify-content:center;
                   background:var(--palette); border-radius:4px; font-size:13px;
                   font-weight:600; cursor:pointer; color:var(--ink); text-decoration:none; }}
  .palette-item:hover {{ background:#dbe0e7; }}
  .palette-item.answered {{ background:var(--palette-answered); }}
  .palette-item.current {{ background:var(--palette-active); color:#fff; }}
  .actions {{ margin-top:20px; padding-top:20px; border-top:1px solid var(--border);
              display:flex; flex-direction:column; gap:8px; }}
  .btn {{ background:var(--accent); color:#fff; border:none; padding:10px 18px;
          border-radius:6px; font-size:14px; font-weight:600; cursor:pointer; width:100%; }}
  .btn:hover {{ background:var(--accent-2); }}
  .btn.secondary {{ background:transparent; color:var(--muted); border:1px solid var(--border); }}

  /* Yuqoridagi audio toolbar — har part uchun katta ijro tugmasi */
  .audio-toolbar {{
    display:flex; align-items:center; gap:14px; margin-bottom:18px;
    padding:14px 18px; background:linear-gradient(135deg,#EFF6FF,#F5F3FF);
    border:1px solid #C7D2FE; border-radius:12px;
  }}
  .play-big {{
    background:linear-gradient(135deg,#2563EB,#7C3AED); color:#FFF;
    border:none; border-radius:10px; padding:12px 22px; font-size:15px;
    font-weight:800; cursor:pointer; box-shadow:0 4px 12px rgba(37,99,235,.25);
    display:inline-flex; align-items:center; gap:8px;
  }}
  .play-big:hover {{ filter:brightness(1.05); }}
  .play-hint {{ color:#4338CA; font-size:12px; }}
  .audio-time {{ margin-left:auto; color:var(--muted); font-size:13px;
                 font-family:monospace; min-width:80px; text-align:right; }}

  /* Part pastidagi navigatsiya paneli */
  .part-nav-bottom {{
    display:flex; justify-content:space-between; align-items:center;
    margin-top:26px; padding-top:18px; border-top:1px solid var(--border);
    gap:12px; flex-wrap:wrap;
  }}
  .part-nav-bottom button {{
    padding:10px 22px; border-radius:8px; font-weight:700; font-size:14px;
    cursor:pointer; border:none;
  }}
  .btn-prev {{ background:#F1F5F9; color:#334155; }}
  .btn-next {{ background:var(--accent); color:#FFF; }}
  .btn-finish {{ background:linear-gradient(135deg,#10B981,#059669); color:#FFF;
                 padding:12px 28px !important; font-size:15px !important; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="sub">IELTS Listening Test</div>
</header>
<div class="tabs">{tabs_html}</div>
<main>
  <section class="content">{parts_html}</section>
  <aside>
    <h3>Question Palette</h3>
    <div id="palette">{palette_html}</div>
    <div class="actions">
      <button class="btn" id="submit-btn" style="background:linear-gradient(135deg,#10B981,#059669);">
        ✓ Tugatish va tekshirish
      </button>
      <button class="btn secondary" id="clear-btn">Clear all</button>
    </div>
  </aside>
</main>

<script>
const TOTAL_QUESTIONS = {total_questions};

function switchPart(n) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', +t.dataset.part === n));
  document.querySelectorAll('.part').forEach(p => p.classList.toggle('active', +p.dataset.part === n));
  document.querySelectorAll('.palette-item').forEach(it =>
    it.classList.toggle('current', +it.dataset.part === n));
  // Bir vaqtda faqat bitta audio ijro etadi — boshqalarini to'xtatamiz
  document.querySelectorAll('.part-audio').forEach(a => {{
    if (+a.dataset.part !== n) {{ a.pause(); }}
  }});
  updatePlayButtons();
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

// -------- Audio: katta tugma + Space/Ctrl yorliqlar --------
function fmtTime(t) {{
  t = Math.max(0, Math.floor(t || 0));
  return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0');
}}
function currentAudio() {{
  const p = document.querySelector('.part.active');
  return p ? p.querySelector('.part-audio') : null;
}}
function togglePlay() {{
  const a = currentAudio();
  if (!a) return;
  if (a.paused) {{
    document.querySelectorAll('.part-audio').forEach(o => {{ if (o !== a) o.pause(); }});
    a.play().catch(() => {{}});
  }} else {{
    a.pause();
  }}
}}
function updatePlayButtons() {{
  document.querySelectorAll('.play-big').forEach(btn => {{
    const partNum = +btn.dataset.targetPart;
    const audio = document.querySelector(`.part-audio[data-part="${{partNum}}"]`);
    const icon = btn.querySelector('.play-icon');
    const label = btn.querySelector('.play-label');
    if (audio && !audio.paused) {{
      if (icon) icon.textContent = '⏸';
      if (label) label.textContent = 'To\\'xtatish';
    }} else {{
      if (icon) icon.textContent = '▶';
      if (label) label.textContent = 'Audio boshlash';
    }}
  }});
}}
document.querySelectorAll('.play-big').forEach(btn => {{
  btn.addEventListener('click', () => togglePlay());
}});
document.querySelectorAll('.part-audio').forEach(audio => {{
  const partNum = +audio.dataset.part;
  const timeEl = document.querySelector(`[data-time-for="${{partNum}}"]`);
  audio.addEventListener('play', updatePlayButtons);
  audio.addEventListener('pause', updatePlayButtons);
  audio.addEventListener('ended', updatePlayButtons);
  audio.addEventListener('timeupdate', () => {{
    if (timeEl) timeEl.textContent = fmtTime(audio.currentTime) + ' / ' + fmtTime(audio.duration);
  }});
  audio.addEventListener('loadedmetadata', () => {{
    if (timeEl) timeEl.textContent = '0:00 / ' + fmtTime(audio.duration);
  }});
}});

// Space: input tashqarisida play/pause. Ctrl (yolg'iz) ham play/pause —
// foydalanuvchi so'ragan yorliq (input ichida ham ishlaydi).
document.addEventListener('keydown', (e) => {{
  if (e.key === 'Control' && !e.repeat && !e.altKey && !e.shiftKey) {{
    e.preventDefault();
    togglePlay();
    return;
  }}
  const tag = (e.target.tagName || '').toLowerCase();
  const inField = tag === 'input' || tag === 'textarea' || tag === 'select'
                  || e.target.isContentEditable;
  if (inField) return;
  if (e.code === 'Space') {{
    e.preventDefault();
    togglePlay();
  }}
}});

// Part navigatsiyasi (pastdagi tugmalar)
document.querySelectorAll('[data-goto-part]').forEach(btn => {{
  btn.addEventListener('click', () => switchPart(+btn.dataset.gotoPart));
}});
const finishInline = document.getElementById('finish-btn-inline');
if (finishInline) {{
  finishInline.addEventListener('click', () => {{
    document.getElementById('submit-btn').click();
  }});
}}
document.querySelectorAll('.tab').forEach(t =>
  t.addEventListener('click', () => switchPart(+t.dataset.part)));
document.querySelectorAll('.palette-item').forEach(it =>
  it.addEventListener('click', (e) => {{
    e.preventDefault();
    const n = +it.dataset.part, q = it.dataset.q;
    switchPart(n);
    const target = document.querySelector(`.part[data-part="${{n}}"] [data-q="${{q}}"], .part[data-part="${{n}}"] [data-q*="-${{q}}-"], .part[data-part="${{n}}"] [data-q^="${{q}}-"], .part[data-part="${{n}}"] [data-q$="-${{q}}"]`);
    if (target) {{
      target.scrollIntoView({{behavior:'smooth', block:'center'}});
      if (target.tagName === 'INPUT' && target.type === 'text') target.focus();
    }}
  }}));
switchPart(1);

document.querySelectorAll('input[type="checkbox"][data-limit]').forEach(cb =>
  cb.addEventListener('change', () => {{
    const limit = +cb.dataset.limit;
    const group = document.querySelectorAll(`input[type="checkbox"][name="${{cb.name}}"]`);
    if ([...group].filter(x => x.checked).length > limit) cb.checked = false;
    updatePalette();
  }}));

let draggedCard = null;
document.querySelectorAll('.dnd-card').forEach(card => {{
  card.addEventListener('dragstart', (e) => {{
    if (card.classList.contains('used')) {{ e.preventDefault(); return; }}
    draggedCard = card;
    e.dataTransfer.setData('text/plain', card.dataset.value);
    e.dataTransfer.effectAllowed = 'move';
  }});
  card.addEventListener('dragend', () => {{ draggedCard = null; }});
}});
document.querySelectorAll('.options-drop-zone').forEach(zone => {{
  zone.addEventListener('dragover', (e) => {{ e.preventDefault(); zone.classList.add('drag-over'); }});
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {{
    e.preventDefault();
    zone.classList.remove('drag-over');
    if (!draggedCard) return;
    dropCardIntoZone(draggedCard, zone);
  }});
  zone.addEventListener('click', () => {{
    if (zone.classList.contains('filled')) clearZone(zone);
  }});
}});
function dropCardIntoZone(card, zone) {{
  if (zone.classList.contains('filled')) {{
    const prevVal = zone.querySelector('.dnd-drop-value').dataset.value;
    releaseCard(prevVal);
  }}
  document.querySelectorAll('.options-drop-zone.filled').forEach(z => {{
    if (z !== zone && z.querySelector('.dnd-drop-value').dataset.value === card.dataset.value) {{
      z.classList.remove('filled');
      z.querySelector('.dnd-drop-value').textContent = '';
      z.querySelector('input[type="hidden"]').value = '';
    }}
  }});
  const valSpan = zone.querySelector('.dnd-drop-value');
  valSpan.textContent = card.dataset.text || card.dataset.value;
  valSpan.dataset.value = card.dataset.value;
  zone.querySelector('input[type="hidden"]').value = card.dataset.value;
  zone.classList.add('filled');
  card.classList.add('used');
  updatePalette();
}}
function clearZone(zone) {{
  const val = zone.querySelector('.dnd-drop-value').dataset.value;
  releaseCard(val);
  zone.classList.remove('filled');
  zone.querySelector('.dnd-drop-value').textContent = '';
  zone.querySelector('.dnd-drop-value').dataset.value = '';
  zone.querySelector('input[type="hidden"]').value = '';
  updatePalette();
}}
function releaseCard(value) {{
  const card = document.querySelector(`.dnd-card[data-value="${{value}}"]`);
  if (card) card.classList.remove('used');
}}

function updatePalette() {{
  const answered = new Set();
  document.querySelectorAll('input[type="text"][data-q]').forEach(i => {{
    if (i.value.trim()) String(i.dataset.q).split('-').forEach(q => answered.add(+q));
  }});
  document.querySelectorAll('input[type="hidden"][data-q]').forEach(i => {{
    if (i.value.trim()) answered.add(+i.dataset.q);
  }});
  document.querySelectorAll('input[type="radio"]:checked').forEach(i =>
    String(i.dataset.q).split('-').forEach(q => answered.add(+q)));
  const groups = {{}};
  document.querySelectorAll('input[type="checkbox"]:checked').forEach(i =>
    groups[i.dataset.q] = (groups[i.dataset.q] || 0) + 1);
  Object.entries(groups).forEach(([key, count]) => {{
    key.split('-').map(Number).slice(0, count).forEach(q => answered.add(q));
  }});
  document.querySelectorAll('.palette-item').forEach(it =>
    it.classList.toggle('answered', answered.has(+it.dataset.q)));
}}
document.addEventListener('input', updatePalette);
document.addEventListener('change', updatePalette);

function collectAnswers() {{
  const ans = {{}};
  document.querySelectorAll('input[type="text"][data-q]').forEach(i => {{
    if (i.value.trim()) ans[i.dataset.q] = i.value.trim();
  }});
  document.querySelectorAll('input[type="radio"]:checked').forEach(i => {{
    ans[i.dataset.q] = i.value;
  }});
  const cbGroups = {{}};
  document.querySelectorAll('input[type="checkbox"]:checked').forEach(i => {{
    (cbGroups[i.dataset.q] = cbGroups[i.dataset.q] || []).push(i.value);
  }});
  Object.entries(cbGroups).forEach(([key, values]) => {{
    const parts = key.split('-');
    if (parts.length === values.length) {{
      parts.forEach((p, idx) => ans[p] = values[idx]);
    }} else {{
      ans[key] = values;
    }}
  }});
  document.querySelectorAll('input[type="hidden"][data-q]').forEach(i => {{
    if (i.value.trim()) ans[i.dataset.q] = i.value.trim();
  }});
  return ans;
}}

// Sayt sahifasi bilan aloqa: submit natijasini postMessage orqali jo'natamiz.
document.getElementById('submit-btn').addEventListener('click', () => {{
  const ans = collectAnswers();
  try {{
    window.parent.postMessage({{ type: 'ielts:submit', answers: ans }}, '*');
  }} catch (e) {{
    console.error('postMessage failed', e);
  }}
}});
document.getElementById('clear-btn').addEventListener('click', () => {{
  if (!confirm('Clear all answers?')) return;
  document.querySelectorAll('input[type="text"]').forEach(i => i.value = '');
  document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(i => i.checked = false);
  document.querySelectorAll('.options-drop-zone.filled').forEach(clearZone);
  updatePalette();
}});

// Ota sahifa bilan aloqa:
//   - 'ielts:reveal' — natija keldi, palette'ni rangli qilamiz
//   - 'ielts:request-submit' — ota sahifa "Tugatish" bosgan, javoblarni yig'ib jo'natamiz
window.addEventListener('message', (e) => {{
  const data = e.data || {{}};
  if (data.type === 'ielts:reveal') {{
    const results = data.results || {{}};
    Object.entries(results).forEach(([q, ok]) => {{
      const item = document.querySelector(`.palette-item[data-q="${{q}}"]`);
      if (item) {{
        item.style.background = ok ? '#059669' : '#B91C1C';
        item.style.color = '#fff';
      }}
    }});
    return;
  }}
  if (data.type === 'ielts:request-submit') {{
    const ans = collectAnswers();
    try {{
      window.parent.postMessage({{ type: 'ielts:submit', answers: ans }}, '*');
    }} catch (err) {{ console.error('postMessage failed', err); }}
  }}
}});
</script>
</body>
</html>
"""


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def _fetch(url: str) -> BeautifulSoup:
    """Sahifani yuklaydi. engnovate.com (va shu kabi saytlar) ba'zan
    Cloudflare tipida JS-challenge qaytaradi — oddiy `requests` 403 oladi.
    Shu bois avval `cloudscraper` bilan urinamiz (JS challenge'ni yechadi),
    keyin oddiy `requests` (fallback)."""
    text = None
    try:
        import cloudscraper  # type: ignore
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            delay=10,
        )
        # cloudscraper o'z headerlarini o'zi qo'yadi — bizniki bilan
        # aralashtirsak challenge yechilmaydi (custom User-Agent JS testni
        # bo'shatib qo'yishi mumkin).
        r = scraper.get(url, timeout=45)
        r.raise_for_status()
        text = r.text
    except Exception as cs_exc:  # cloudscraper yo'q yoki xato — oddiy requests
        try:
            r = requests.get(url, headers=_BROWSER_HEADERS, timeout=30)
            r.raise_for_status()
            text = r.text
        except requests.RequestException as exc:
            raise IeltsParseError(
                f"URL yetib bo'lmadi: {exc} "
                f"(cloudscraper ham: {cs_exc.__class__.__name__})"
            ) from exc
    return BeautifulSoup(text, "html.parser")


def _clean_inputs(section: Tag) -> None:
    for item in section.select(".ielts-listening-question-item"):
        nums = [n.get_text(strip=True) for n in item.select(".ielts-listening-question-number")
                if n.get_text(strip=True).isdigit()]
        if not nums:
            continue
        data_q = "-".join(nums) if len(nums) > 1 else nums[0]

        drop_zone = item.select_one(".options-drop-zone")
        if drop_zone:
            hidden = drop_zone.find("input", {"type": "hidden"})
            if hidden is not None:
                hidden["data-q"] = data_q
                for attr in ("name", "id"):
                    hidden.attrs.pop(attr, None)
            drop_zone.attrs.pop("data-dnd-group", None)
            drop_zone["data-q"] = data_q
            continue

        inputs = item.find_all("input")
        if not inputs:
            continue

        text_inputs = [i for i in inputs if i.get("type", "text") == "text"]
        for idx, inp in enumerate(text_inputs):
            q = nums[idx] if idx < len(nums) else nums[-1]
            inp["data-q"] = q
            for attr in ("name", "id", "aria-label"):
                inp.attrs.pop(attr, None)

        for inp in inputs:
            t = inp.get("type", "text")
            if t in ("radio", "checkbox"):
                inp["data-q"] = data_q
                inp["name"] = f"q{data_q.replace('-', '_')}"
                inp.attrs.pop("id", None)
        for lbl in item.find_all("label"):
            lbl.attrs.pop("for", None)

    for panel in section.select(".dnd-panel--matching"):
        panel.attrs.pop("data-dnd-group", None)

    for empty in section.select(".ielts-listening-question-item"):
        if not empty.get_text(strip=True) and not empty.find(["input", "select"]):
            empty.decompose()


def _extract_parts(soup: BeautifulSoup) -> list[dict]:
    parts = []
    audios = [a.get("src") for a in soup.select("audio source") if a.get("src")]

    part_els = soup.select(".ielts-listening-question-section")
    for idx, part in enumerate(part_els):
        num = int(part.get("data-part-number", idx + 1))

        # Wrapper — BeautifulSoup obyekti bo'ladi (Tag emas), aks holda
        # `new_tag` chaqirilishi mumkin emas. Rendering'da .decode_contents()
        # ishlagani sabab qo'shimcha <html> qavati ham chiqmaydi.
        wrapper = BeautifulSoup("", "html.parser")
        for child in part.children:
            if not isinstance(child, Tag):
                continue
            classes = child.get("class") or []
            if "ielts-listening-question-section-heading" in classes:
                raw = child.get_text(strip=True).replace("\xa0", " ")
                head_text = raw.split("Practice this section only")[0].strip()
                if head_text:
                    h3 = wrapper.new_tag("h3")
                    h3.string = head_text
                    wrapper.append(h3)
            elif ("ielts-listening-question-section-content" in classes
                  or "ielts-listening-questions" in classes):
                wrapper.append(child)

        _clean_inputs(wrapper)

        question_nums = sorted({
            int(n.get_text(strip=True))
            for n in wrapper.select(".ielts-listening-question-number")
            if n.get_text(strip=True).isdigit()
        })

        parts.append({
            "num": num,
            "questions": question_nums,
            "html": wrapper.decode_contents(),
            "audio": audios[idx] if idx < len(audios) else "",
        })
    return parts


def _build_html(soup: BeautifulSoup, parts: list[dict]) -> tuple[str, int, str]:
    title = soup.title.string.strip() if soup.title and soup.title.string else "IELTS Listening Test"

    tabs_html = "".join(
        f'<button class="tab{" active" if p["num"] == 1 else ""}" '
        f'data-part="{p["num"]}">Part {p["num"]}</button>'
        for p in parts
    )
    parts_html = ""
    total_parts = len(parts)
    for idx, p in enumerate(parts):
        qs = p["questions"]
        rng = f"{qs[0]}–{qs[-1]}" if qs else ""
        # Audio toolbar — katta ijro tugmasi + hidden audio element (native
        # controls yashiringan, o'z tugmalarimiz orqali boshqariladi).
        if p["audio"]:
            audio_block = (
                f'<div class="audio-toolbar">'
                f'  <button type="button" class="play-big" data-target-part="{p["num"]}">'
                f'    <span class="play-icon">▶</span><span class="play-label">Audio boshlash</span>'
                f'  </button>'
                f'  <div class="play-hint">Space bilan ham play/pause</div>'
                f'  <div class="audio-time" data-time-for="{p["num"]}">0:00 / 0:00</div>'
                f'  <audio class="part-audio" data-part="{p["num"]}" '
                f'         src="{p["audio"]}" preload="metadata"></audio>'
                f'</div>'
            )
        else:
            audio_block = (
                '<div class="audio-toolbar" style="background:#FEF2F2;border-color:#FCA5A5;">'
                '<div style="color:#991B1B;font-weight:700;">⚠ Bu part uchun audio topilmadi.</div>'
                '</div>'
            )
        # Part pastidagi navigatsiya
        nav_buttons = []
        if idx > 0:
            nav_buttons.append(
                f'<button type="button" class="btn-prev" data-goto-part="{parts[idx-1]["num"]}">← Oldingi Part</button>'
            )
        else:
            nav_buttons.append('<span></span>')  # spacing
        if idx < total_parts - 1:
            nav_buttons.append(
                f'<button type="button" class="btn-next" data-goto-part="{parts[idx+1]["num"]}">Keyingi Part →</button>'
            )
        else:
            nav_buttons.append(
                '<button type="button" class="btn-finish" id="finish-btn-inline">✓ Tugatish va tekshirish</button>'
            )
        nav_block = f'<div class="part-nav-bottom">{"".join(nav_buttons)}</div>'

        parts_html += (
            f'<div class="part{" active" if p["num"] == 1 else ""}" data-part="{p["num"]}">'
            f'<h2>Part {p["num"]} — Questions {rng}</h2>'
            f'{audio_block}{p["html"]}{nav_block}'
            f'</div>'
        )
    palette_html = ""
    for p in parts:
        items = "".join(
            f'<a href="#" class="palette-item" data-part="{p["num"]}" data-q="{q}">{q}</a>'
            for q in p["questions"]
        )
        palette_html += (
            f'<div class="palette-section"><strong>Part {p["num"]}</strong>'
            f'<div class="palette-grid">{items}</div></div>'
        )
    total = sum(len(p["questions"]) for p in parts)
    html = PAGE_TEMPLATE.format(
        title=title,
        tabs_html=tabs_html,
        parts_html=parts_html,
        palette_html=palette_html,
        total_questions=total,
    )
    return html, total, title


def parse_test(url: str) -> dict:
    """`{"title", "html", "total_questions"}` qaytaradi. Xato bo'lsa
    `IeltsParseError` ko'tariladi (worker uni user-facing xatoga aylantiradi)."""
    soup = _fetch(url)
    parts = _extract_parts(soup)
    if not parts:
        raise IeltsParseError(
            "Sahifada `.ielts-listening-question-section` topilmadi — URL "
            "engnovate.com testi bo'lishi kerak."
        )
    html, total, title = _build_html(soup, parts)
    if total == 0:
        raise IeltsParseError("Savollar topilmadi (0 ta) — sahifa noto'g'ri.")
    return {"html": html, "title": title, "total_questions": total}
