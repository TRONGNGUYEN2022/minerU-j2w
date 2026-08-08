import base64
import io
import json
import os
import re
import zipfile
import time
from bs4 import BeautifulSoup
import requests
import streamlit as st
import streamlit.components.v1 as components

# Import thư viện mistralai SDK 2.0
try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title="Convert PDF/Image to word", page_icon="📐", layout="wide")

# --- KHỞI TẠO SESSION STATE ---
if "active_mistral_pages" not in st.session_state:
    st.session_state.active_mistral_pages = []
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = ""

# --- HÀM XỬ LÝ ZIP MISTRAL NÂNG CAO ---
def extract_mistral_zip_advanced(zip_bytes):
    images_dict = {}
    pages_data = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        page_dirs = set()
        for f in namelist:
            match = re.search(r"pages/(page-\d+)/", f)
            if match:
                page_dirs.add(match.group(1))
        
        sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
        
        for p_dir in sorted_pages:
            page_info = {"page_name": p_dir, "markdown": "", "images": {}}
            md_path = f"pages/{p_dir}/markdown.md"
            if md_path in namelist:
                try:
                    page_info["markdown"] = z.read(md_path).decode("utf-8")
                except: pass
                
            for f in namelist:
                if f.startswith(f"pages/{p_dir}/") and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                    img_name = os.path.basename(f)
                    img_bytes = z.read(f)
                    page_info["images"][img_name] = img_bytes
                    images_dict[img_name] = img_bytes
                    
            pages_data.append(page_info)
    return pages_data, images_dict

# --- GỌI API MISTRAL OCR ---
def process_with_mistral(uploaded_file, mistral_api_key, selected_model):
    if not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai`.")
        return []
    try:
        client = Mistral(api_key=mistral_api_key)
        file_bytes = uploaded_file.getvalue()
        base64_file = base64.b64encode(file_bytes).decode('utf-8')

        ocr_response = client.ocr.process(
            document={"type": "document_url", "document_url": f"data:application/pdf;base64,{base64_file}"},
            model=selected_model,
            include_image_base64=True,
            include_blocks=True
        )
        
        pages_data = []
        if hasattr(ocr_response, "pages"):
            for idx, page in enumerate(ocr_response.pages):
                p_dir = f"page-{idx+1}"
                page_info = {"page_name": p_dir, "markdown": "", "images": {}}
                if hasattr(page, "markdown"):
                    page_info["markdown"] = page.markdown
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_b64 = img.image_base64
                            if "," in img_b64: img_b64 = img_b64.split(",")[1]
                            try: page_info["images"][f"{img.id}.jpeg"] = base64.b64decode(img_b64)
                            except: pass
                pages_data.append(page_info)
        return pages_data
    except Exception as e:
        st.error(f"Lỗi Mistral OCR: {e}")
        return []

# --- RENDER KHUNG PREVIEW CHUẨN GIAO DIỆN ---
def render_mistral_markdown_preview(pages_data, file_name="Document"):
    full_md_combined = ""
    for page in pages_data:
        p_name = page["page_name"]
        md_text = page["markdown"]
        images = page["images"]
        
        for img_name, img_bytes in images.items():
            if img_name in md_text:
                encoded = base64.b64encode(img_bytes).decode("utf-8")
                md_text = md_text.replace(f"({img_name})", f"(data:image/jpeg;base64,{encoded})")
                
        full_md_combined += f"\n\n<hr/>\n<h3>Trang {p_name.split('-')[1]}</h3>\n\n" + md_text

    preview_component = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {{delimiters: [{{left: '$', right: '$', display: false}}, {{left: '$$', right: '$$', display: true}}]}});"></script>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/html-docx-js@0.3.1/dist/html-docx.js"></script>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 10px; background-color: #ffffff; }}
            .btn-action {{ padding: 10px 20px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 10px; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .btn-copy {{ background-color: #2b6cb0; }}
            .btn-copy:hover {{ background-color: #2c5282; }}
            .btn-word {{ background-color: #2f855a; }}
            .btn-word:hover {{ background-color: #276749; }}
            #status-msg {{ margin-left: 10px; color: #2f855a; font-weight: bold; font-size: 13px; display: none; }}
            .preview-card {{ background-color: #ffffff; padding: 30px; border-radius: 10px; border: 1px solid #cbd5e0; max-height: 600px; overflow-y: auto; }}
            img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; border-radius: 6px; }}
        </style>
    </head>
    <body>
        <div>
            <button class="btn-action btn-copy" onclick="copyContentToClipboard()">📋 Sao chép nhanh (Dán vào Word)</button>
            <button class="btn-action btn-word" onclick="saveAsWordDocx()">💾 Lưu thành file Word (.docx)</button>
            <span id="status-msg">✔ Thao tác thành công!</span>
        </div>
        <div class="preview-card" id="preview-box"></div>
        <script>
            const markdownText = {json.dumps(full_md_combined)};
            document.getElementById('preview-box').innerHTML = marked.parse(markdownText);
            renderMathInElement(document.getElementById('preview-box'));

            function copyContentToClipboard() {{
                const range = document.createRange();
                range.selectNode(document.getElementById('preview-box'));
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                try {{
                    document.execCommand('copy');
                    showStatus("Đã sao chép vào bộ nhớ tạm! Mở Word và nhấn Ctrl+V");
                }} catch (err) {{ alert('Không thể sao chép tự động!'); }}
                window.getSelection().removeAllRanges();
            }}

            function saveAsWordDocx() {{
                const contentHTML = document.getElementById('preview-box').innerHTML;
                const converted = htmlDocx.asBlob('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + contentHTML + '</body></html>');
                const link = document.createElement('a');
                link.href = URL.createObjectURL(converted);
                link.download = "{file_name}_Mistral.docx";
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                showStatus("Đã tải xuống file Word thành công!");
            }}

            function showStatus(msg) {{
                const status = document.getElementById('status-msg');
                status.innerText = "✔ " + msg;
                status.style.display = 'inline';
                setTimeout(() => {{ status.style.display = 'none'; }}, 4000);
            }}
        </script>
    </body>
    </html>
    """
    st.markdown("### 👁️ Bản xem trước Markdown & Hình ảnh từ Mistral OCR")
    components.html(preview_component, height=750, scrolling=False)


# --- 5. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2, tab3 = st.tabs([
    "🌪️ Mistral OCR Server", 
    "🌐 MinerU Web Extractor", 
    "📁 Tải file ZIP kết quả (Offline)"
])

with tab1:
    st.subheader("🌪️ Cấu hình Mistral OCR")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="t2")
    
    if st.button("📤 Gửi & Xử lý qua Mistral", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang xử lý qua Mistral OCR..."):
                pages_data = process_with_mistral(mistral_file, st.session_state.saved_mistral_key, "mistral-ocr-latest")
                if pages_data:
                    st.session_state.active_mistral_pages = pages_data
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Hoàn tất xử lý qua Mistral OCR!")
                    st.rerun()

with tab2:
    st.subheader("🌐 MinerU Web Extractor")
    components.iframe("https://mineru.net/OpenSourceTools/Extractor", height=650, scrolling=True)

with tab3:
    st.subheader("📁 Tải file ZIP kết quả Mistral Offline")
    uploaded_zip = st.file_uploader("Chọn file ZIP kết quả Mistral", type=["zip"], key="offline_zip")
    
    if uploaded_zip:
        pages_data, _ = extract_mistral_zip_advanced(uploaded_zip.getvalue())
        if pages_data:
            st.session_state.active_mistral_pages = pages_data
            st.session_state.active_file_name = uploaded_zip.name.rsplit(".", 1)[0]
            st.success("Đã nạp file ZIP Mistral thành công!")
            st.rerun()

# Điều phối khung xem trước
if st.session_state.active_mistral_pages:
    st.divider()
    render_mistral_markdown_preview(
        st.session_state.active_mistral_pages,
        file_name=st.session_state.active_file_name
    )