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

try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

st.set_page_config(page_title="Convert PDF/Image to word", page_icon="📐", layout="wide")

UNIFIED_IMAGE_DIR = "images"
os.makedirs(UNIFIED_IMAGE_DIR, exist_ok=True)

if "active_markdown_content" not in st.session_state:
    st.session_state.active_markdown_content = ""
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = ""

# --- HÀM XỬ LÝ ĐỌC ZIP VÀ GOM ẢNH CHUẨN XÁC ---
def process_markdown_zip(zip_bytes):
    images_dict = {}
    page_mds = []
    
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        
        # Tìm các thư mục trang page-X theo đúng thứ tự cuốn chiếu
        page_dirs = set()
        for f in namelist:
            match = re.search(r"pages/(page-\d+)/", f)
            if match: page_dirs.add(match.group(1))
        sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
        
        # Đọc cuốn chiếu markdown và ảnh của từng trang từ thư mục pages/
        for p_dir in sorted_pages:
            md_path = f"pages/{p_dir}/markdown.md"
            if md_path in namelist:
                try:
                    md_text = z.read(md_path).decode("utf-8")
                    page_mds.append(f"\n\n<hr/><h3 style='color: #2b6cb0;'>Trang {p_dir.split('-')[1]}</h3>\n\n" + md_text)
                except: pass
                
        root_markdown = "".join(page_mds)
        if not root_markdown and "markdown.md" in namelist:
            root_markdown = z.read("markdown.md").decode("utf-8")
            
        # Gom toàn bộ file ảnh vào thư mục images/ và bộ nhớ
        for f in namelist:
            if ("pages/" in f or "images/" in f or "/" not in f) and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                img_name = os.path.basename(f)
                if img_name:
                    img_bytes = z.read(f)
                    images_dict[img_name] = img_bytes
                    with open(os.path.join(UNIFIED_IMAGE_DIR, img_name), "wb") as img_f:
                        img_f.write(img_bytes)
                        
    return root_markdown, images_dict

# --- HÀM GỌI API MISTRAL OCR ---
def process_markdown_api(uploaded_file, mistral_api_key, selected_model):
    if not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai`.")
        return "", {}
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
        
        full_markdown = ""
        images_dict = {}
        
        if hasattr(ocr_response, "pages"):
            for idx, page in enumerate(ocr_response.pages):
                page_md = page.markdown if hasattr(page, "markdown") else ""
                full_markdown += f"\n\n<hr/><h3 style='color: #2b6cb0;'>Trang {idx+1}</h3>\n\n" + page_md
                
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_id = img.id
                            img_b64 = img.image_base64
                            if "," in img_b64: img_b64 = img_b64.split(",")[1]
                            try:
                                img_bytes = base64.b64decode(img_b64)
                                img_filename = f"{img_id}.jpeg"
                                images_dict[img_filename] = img_bytes
                                with open(os.path.join(UNIFIED_IMAGE_DIR, img_filename), "wb") as img_f:
                                    img_f.write(img_bytes)
                            except: pass
                            
        return full_markdown, images_dict
    except Exception as e:
        st.error(f"Lỗi Mistral OCR: {e}")
        return "", {}

def get_image_bytes_helper(img_name, images_dict):
    if not img_name: return None
    clean_name = os.path.basename(img_name)
    if images_dict and clean_name in images_dict:
        return images_dict[clean_name]
    local_path = os.path.join(UNIFIED_IMAGE_DIR, clean_name)
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            return f.read()
    return None

# --- RENDER PREVIEW HOÀN CHỈNH ---
def render_markdown_preview(markdown_content, images_dict, file_name="document"):
    processed_html = markdown_content
    
    # Quét và thay thế chính xác các thẻ ảnh markdown bằng chuỗi base64 thực tế
    def replace_img_tag(match):
        img_filename = os.path.basename(match.group(2))
        ibytes = get_image_bytes_helper(img_filename, images_dict)
        if ibytes:
            enc = base64.b64encode(ibytes).decode("utf-8")
            return f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{enc}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>'
        return match.group(0)

    processed_html = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_img_tag, processed_html)
    formatted_html = processed_html.replace("\n", "<br>")

    preview_inner_html = f'''
    <div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">
        {formatted_html}
    </div>
    '''
    
    copier_component = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {{delimiters: [{{left: '$', right: '$', display: false}}, {{left: '$$', right: '$$', display: true}}, {{left: '\\\\(', right: '\\\\)', display: false}}, {{left: '\\\\[', right: '\\\\]', display: true}}]}});"></script>
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
        <div class="preview-card" id="preview-box">
            {preview_inner_html}
        </div>
        <script>
        function copyContentToClipboard() {{
            const range = document.createRange();
            range.selectNode(document.getElementById('content-to-copy'));
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
            link.download = "{file_name}_Fixed.docx";
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
    st.markdown("### 👁️ Bản xem trước Chuẩn xác Từng Trang & Chèn Ảnh")
    components.html(copier_component, height=750, scrolling=False)


# --- 5. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2 = st.tabs([
    "🌪️ Mistral OCR (Parser)", 
    "📁 Tải file ZIP kết quả Offline"
])

with tab1:
    st.subheader("🌪️ Cấu hình Mistral OCR")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="t2")
    
    if st.button("📤 Gửi & Xử lý", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang xử lý lấy file markdown và chèn ảnh..."):
                md_content, imgs = process_markdown_api(mistral_file, st.session_state.saved_mistral_key, "mistral-ocr-latest")
                if md_content:
                    st.session_state.active_markdown_content = md_content
                    st.session_state.active_images_dict = imgs
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Hoàn tất xử lý thành công!")
                    st.rerun()

with tab2:
    st.subheader("📁 Tải file ZIP kết quả Mistral Offline")
    uploaded_zip = st.file_uploader("Chọn file ZIP kết quả Mistral", type=["zip"], key="offline_zip")
    
    if uploaded_zip:
        md_content, imgs = process_markdown_zip(uploaded_zip.getvalue())
        if md_content:
            st.session_state.active_markdown_content = md_content
            st.session_state.active_images_dict = imgs
            st.session_state.active_file_name = uploaded_zip.name.rsplit(".", 1)[0]
            st.success("Đã nạp file ZIP thành công!")
            st.rerun()

if st.session_state.active_markdown_content:
    st.divider()
    render_markdown_preview(
        st.session_state.active_markdown_content,
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )