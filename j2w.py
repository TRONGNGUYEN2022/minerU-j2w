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

UNIFIED_IMAGE_DIR = "images"
os.makedirs(UNIFIED_IMAGE_DIR, exist_ok=True)

# --- KHỞI TẠO SESSION STATE ---
if "active_json" not in st.session_state:
    st.session_state.active_json = None
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = ""

# --- HÀM ĐỌC VÀ GỘP JSON CHUẨN TỪ MISTRAL ZIP ---
def unify_mistral_zip(zip_bytes):
    images_dict = {}
    all_pages_data = []
    
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        page_dirs = set()
        for f in namelist:
            match = re.search(r"pages/(page-\d+)/", f)
            if match:
                page_dirs.add(match.group(1))
        
        sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
        
        for p_dir in sorted_pages:
            page_blocks = []
            meta_path = f"pages/{p_dir}/page-metadata.json"
            if meta_path in namelist:
                try:
                    meta_content = json.loads(z.read(meta_path).decode("utf-8"))
                    page_blocks = meta_content.get("blocks", [])
                except: pass
                
            page_images = {}
            for f in namelist:
                if f.startswith(f"pages/{p_dir}/") and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                    img_name = os.path.basename(f)
                    img_bytes = z.read(f)
                    page_images[img_name] = img_bytes
                    images_dict[img_name] = img_bytes
                    
                    with open(os.path.join(UNIFIED_IMAGE_DIR, img_name), "wb") as img_f:
                        img_f.write(img_bytes)
                        
            all_pages_data.append({
                "page_name": p_dir,
                "blocks": page_blocks,
                "images": page_images
            })
            
    return {"pages": all_pages_data}, images_dict

# --- GỌI API MISTRAL OCR VÀ TẠO CẤU TRÚC TRANG ---
def process_with_mistral_unified(uploaded_file, mistral_api_key, selected_model):
    if not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai`.")
        return None, {}
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
        
        all_pages_data = []
        images_dict = {}
        
        if hasattr(ocr_response, "pages"):
            for idx, page in enumerate(ocr_response.pages):
                p_dir = f"page-{idx+1}"
                page_blocks = []
                page_images = {}
                
                if hasattr(page, "markdown") and page.markdown:
                    page_blocks.append({"type": "text", "content": page.markdown})
                    
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_id = img.id
                            img_b64 = img.image_base64
                            if "," in img_b64: img_b64 = img_b64.split(",")[1]
                            try:
                                img_bytes = base64.b64decode(img_b64)
                                img_filename = f"{img_id}.jpeg"
                                page_images[img_filename] = img_bytes
                                images_dict[img_filename] = img_bytes
                                
                                with open(os.path.join(UNIFIED_IMAGE_DIR, img_filename), "wb") as img_f:
                                    img_f.write(img_bytes)
                                    
                                page_blocks.append({
                                    "type": "image",
                                    "imageId": img_filename,
                                    "content": f"![{img_filename}]({img_filename})"
                                })
                            except: pass
                            
                all_pages_data.append({
                    "page_name": p_dir,
                    "blocks": page_blocks,
                    "images": page_images
                })
                            
        return {"pages": all_pages_data}, images_dict
    except Exception as e:
        st.error(f"Lỗi Mistral OCR: {e}")
        return None, {}

def get_unified_image_bytes(img_name, images_dict):
    if not img_name: return None
    clean_name = os.path.basename(img_name)
    if images_dict and clean_name in images_dict:
        return images_dict[clean_name]
    local_path = os.path.join(UNIFIED_IMAGE_DIR, clean_name)
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            return f.read()
    return None

# --- RENDER PREVIEW CHÍNH XÁC THEO TỪNG BLOCK VÀ XỬ LÝ EQUATION ---
def render_unified_json_preview(json_data, images_dict, file_name="document"):
    preview_inner_html = '<div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">'
    
    pages = json_data.get("pages", [])
    for page in pages:
        p_name = page.get("page_name", "page-1")
        blocks = page.get("blocks", [])
        page_images = page.get("images", {})
        all_imgs = {**images_dict, **page_images}
        
        preview_inner_html += f"<hr/><h3 style='color: #2b6cb0; margin-top: 20px;'>Trang {p_name.split('-')[1]}</h3>"
        
        for block in blocks:
            b_type = block.get("type")
            content = block.get("content", "")
            
            if b_type == "image":
                img_id = block.get("imageId")
                if not img_id:
                    m = re.search(r'\((.*?)\)', content)
                    if m: img_id = os.path.basename(m.group(1))
                
                img_bytes = get_unified_image_bytes(img_id, all_imgs)
                if img_bytes:
                    encoded = base64.b64encode(img_bytes).decode("utf-8")
                    preview_inner_html += f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{encoded}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>'
            elif b_type in ["header", "footer"]:
                preview_inner_html += f"<div style='font-size: 13px; color: #a0aec0; margin: 5px 0;'>{content}</div>"
            else:
                formatted_content = content.replace("\n", "<br>")
                preview_inner_html += f"<div style='margin-bottom: 10px;'>{formatted_content}</div>"
                
    preview_inner_html += '</div>'
    
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
            link.download = "{file_name}_Accurate.docx";
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
    st.markdown("### 👁️ Bản xem trước Định vị Ảnh & Công thức Chuẩn xác")
    components.html(copier_component, height=750, scrolling=False)


# --- 5. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2 = st.tabs([
    "🌪️ Mistral OCR (Accurate Parser)", 
    "📁 Tải file ZIP kết quả Offline"
])

with tab1:
    st.subheader("🌪️ Cấu hình Mistral OCR")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="t2")
    
    if st.button("📤 Gửi & Xử lý Định vị Chuẩn", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang xử lý phân tích khối block và định vị ảnh..."):
                u_json, u_imgs = process_with_mistral_unified(mistral_file, st.session_state.saved_mistral_key, "mistral-ocr-latest")
                if u_json:
                    st.session_state.active_json = u_json
                    st.session_state.active_images_dict = u_imgs
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Hoàn tất xử lý định vị chuẩn xác thành công!")
                    st.rerun()

with tab2:
    st.subheader("📁 Tải file ZIP kết quả Mistral Offline")
    uploaded_zip = st.file_uploader("Chọn file ZIP kết quả Mistral", type=["zip"], key="offline_zip")
    
    if uploaded_zip:
        u_json, u_imgs = unify_mistral_zip(uploaded_zip.getvalue())
        if u_json:
            st.session_state.active_json = u_json
            st.session_state.active_images_dict = u_imgs
            st.session_state.active_file_name = uploaded_zip.name.rsplit(".", 1)[0]
            st.success("Đã nạp file ZIP, giải mã block định vị và gom ảnh thành công!")
            st.rerun()

if st.session_state.active_json is not None:
    st.divider()
    render_unified_json_preview(
        st.session_state.active_json,
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )