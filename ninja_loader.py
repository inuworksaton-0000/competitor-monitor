"""Ninja-star (shuriken) loading animation for the Streamlit UI."""

import html

# Note: keep this block free of blank lines and deep indentation,
# otherwise Streamlit's markdown renderer breaks the HTML.
CSS_AND_HTML = """
<style>
.ninja-wrap {
display: flex; align-items: center; gap: 28px;
padding: 22px 26px; margin: 12px 0; border-radius: 16px;
background: linear-gradient(135deg, #1b1646, #0f1630);
border: 1px solid #3b3a8a;
}
.ninja-star { width: 90px; height: 90px; flex: 0 0 90px; animation: ninja-spin 0.9s linear infinite; filter: drop-shadow(0 0 8px rgba(125, 211, 252, 0.55)); }
.ninja-title { color: #e5e7ff; font-size: 20px; font-weight: 700; }
.ninja-msgs { position: relative; height: 24px; margin-top: 6px; }
.ninja-msgs span { position: absolute; left: 0; opacity: 0; color: #a5b4fc; font-size: 15px; animation: ninja-msg 8s infinite; }
.ninja-msgs span:nth-child(2) { animation-delay: 2s; }
.ninja-msgs span:nth-child(3) { animation-delay: 4s; }
.ninja-msgs span:nth-child(4) { animation-delay: 6s; }
@keyframes ninja-spin { to { transform: rotate(360deg); } }
@keyframes ninja-msg { 0% { opacity: 0; } 5% { opacity: 1; } 25% { opacity: 1; } 30% { opacity: 0; } 100% { opacity: 0; } }
</style>
<div class="ninja-wrap">
<svg class="ninja-star" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
<path fill-rule="evenodd" fill="#15151c" stroke="#9aa0ae" stroke-width="3" stroke-linejoin="round" d="M50 4 L61.3 38.7 L96 50 L61.3 61.3 L50 96 L38.7 61.3 L4 50 L38.7 38.7 Z M59 50 A9 9 0 1 0 41 50 A9 9 0 1 0 59 50 Z"/>
</svg>
<div>
<div class="ninja-title">Ninja is scouting __DOMAIN__</div>
<div class="ninja-msgs">
<span>Sneaking past robots.txt...</span>
<span>Reading sitemaps and feeds...</span>
<span>Hunting for fresh blog posts...</span>
<span>Checking pricing pages...</span>
</div>
</div>
</div>
"""


def ninja_loading(placeholder, domain: str) -> None:
    """Render the animation into a Streamlit placeholder (st.empty())."""
    placeholder.markdown(
        CSS_AND_HTML.replace("__DOMAIN__", html.escape(domain)),
        unsafe_allow_html=True,
    )