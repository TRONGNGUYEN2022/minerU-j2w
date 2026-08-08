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
if "active_sequential_pages" not in st.session_state:
    st.session_state.active_sequential_pages = []
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = ""

# --- HÀM XỬ LÝ CUỐN CHIẾU TỪNG TRANG TỪ FILE ZIP ---
def process_sequential_mistral_zip(zip_bytes):
    images_dict = {}
    sequential_pages = []
    
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        page_dirs = set()
        for f in namelist:
            match = re.search(r"pages/(page-\d+)/", f)
            if match:
                page_dirs.add(match.group(1))
        
        # Sắp xếp cuốn chiếu từ trang 1 đến trang cuối
        sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
        
        for p_dir in sorted_pages:
            page_data = {
                "page_name": p_dir,
                "blocks": [],
                "markdown": "",
                "images": {}
            }
            
            # 1. Đọc metadata blocks nếu có
            meta_path = f"pages/{p_dir}/page-metadata.json"
            if meta_path in namelist:
                try:
                    meta_content = json.loads(z.read(meta_path).decode("utf-8"))
                    page_data["blocks"] = meta_content.get("blocks", [])
                except: pass
                
            # 2. Đọc file markdown của trang làm nội dung dự phòng/bổ sung
            md_path = f"pages/{p_dir}/markdown.md"
            if md_path in namelist:
                try:
                    page_data["markdown"] = z.read(md_path).decode("utf-8")
                except: pass
                
            # 3. Trích xuất toàn bộ ảnh của riêng trang này và lưu vào thư mục images
            for f in namelist:
                if f.startswith(f"pages/{p_dir}/") and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                    img_name = os.path.basename(f)
                    img_bytes = z.read(f)
                    page_data["images"][img_name] = img_bytes
                    images_dict[img_name] = img_bytes
                    
                    with open(os.path.join(UNIFIED_IMAGE_DIR, img_name), "wb") as img_f:
                        img_f.write(img_bytes)
                        
            sequential_pages.append(page_data)
            
    return sequential_pages, images_dict

# --- GỌI API MISTRAL OCR VÀ XỬ LÝ CUỐN CHIẾU ---
def process_sequential_mistral_api(uploaded_file, mistral_api_key, selected_model):
    if not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai`.")
        return [], {}
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
        
        sequential_pages = []
        images_dict = {}
        
        if hasattr(ocr_response, "pages"):
            for idx, page in enumerate(ocr_response.pages):
                p_dir = f"page-{idx+1}"
                page_data = {
                    "page_name": p_dir,
                    "blocks": [],
                    "markdown": page.markdown if hasattr(page, "markdown") else "",
                    "images": {}
                }
                
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_id = img.id
                            img_b64 = img.image_base64
                            if "," in img_b64: img_b64 = img_b64.split(",")[1]
                            try:
                                img_bytes = base64.b64decode(img_b64)
                                img_filename = f"{img_id}.jpeg"
                                page_data["images"][img_filename] = img_bytes
                                images_dict[img_filename] = img_bytes
                                
                                with open(os.path.join(UNIFIED_IMAGE_DIR, img_filename), "wb") as img_f:
                                    img_f.write(img_bytes)
                                    
                                page_data["blocks"].append({
                                    "type": "image",
                                    "imageId": img_filename,
                                    "content": f"![{img_filename}]({img_filename})"
                                })
                            except: pass
                            
                # Thêm block text từ markdown
                if page_data["markdown"]:
                    page_data["blocks"].append({
                        "type": "text",
                        "content": page_data["markdown"]
                    })
                    
                sequential_pages.append(page_data)
                
        return sequential_pages, images_dict
    except Exception as e:
        st.error(f"Lỗi Mistral OCR: {e}")
        return [], {}

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

# --- RENDER PREVIEW CUỐN CHIẾU TỪNG TRANG CHUẨN XÁC ---
def render_sequential_preview(sequential_pages, images_dict, file_name="document"):
    preview_inner_html = '<div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">'
    
    for page in sequential_pages:
        p_name = page.get("page_name", "page-1")
        blocks = page.get("blocks", [])
        page_images = page.get("images", {})
        all_imgs = {**images_dict, **page_images}
        
        page_num = p_name.split('-')[1]
        preview_inner_html += f"<hr/><h3 style='color: #2b6cb0; margin-top: 25px;'>Trang {page_num}</h3>"
        
        if blocks:
            # Xử lý theo danh sách blocks tuần tự của trang
            for block in blocks:
                b_type = block.get("type")
                content = block.get("content", "")
                
                if b_type == "image":
                    img_id = block.get("imageId")
                    if not img_id:
                        m = re.search(r'\((.*?)\)', content)
                        if m: img_id = os.path.basename(m.group(1))
                    
                    img_bytes = get_image_bytes_helper(img_id, all_imgs)
                    if img_bytes:
                        encoded = base64.b64encode(img_bytes).decode("utf-8")
                        preview_inner_html += f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{encoded}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>'
                elif b_type in ["header", "footer"]:
                    preview_inner_html += f"<div style='font-size: 13px; color: #a0aec0; margin: 5px 0;'>{content}</div>"
                else:
                    # Kiểm tra và thay thế ảnh trong text nếu có
                    for img_name, ibytes in all_imgs.items():
                        if img_name in content:
                            enc = base64.b64encode(ibytes).decode("utf-8")
                            content = content.replace(f"![{img_name}]({img_name})", f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{enc}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>')
                    
                    formatted_content = content.replace("\n", "<br>")
                    preview_inner_html += f"<div style='margin-bottom: 10px;'>{formatted_content}</div>"
        else:
            # Fallback dùng file markdown nếu không có blocks metadata
            md_content = page.get("markdown", "")
            for img_name, ibytes in all_imgs.items():
                if img_name in md_content:
                    enc = base64.b64encode(ibytes).decode("utf-8")
                    md_content = md_content.replace(f"({img_name})", f"(data:image/jpeg;base64,{enc})")
            preview_inner_html += f"<div style='margin-bottom: 10px;'>{md_content.replace(chr(10), '<br>')}</div>"
                
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
            link.download = "{file_name}_Sequential.docx";
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
    st.markdown("### 👁️ Bản xem trước Xử lý Cuốn chiếu Từng trang")
    components.html(copier_component, height=750, scrolling=False)


# --- 5. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2 = st.tabs([
    "🌪️ Mistral OCR (Sequential)", 
    "📁 Tải file ZIP kết quả Offline"
])

with tab1:
    st.subheader("🌪️ Cấu hình Mistral OCR")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="t2")
    
    if st.button("📤 Gửi & Xử lý Cuốn chiếu", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang xử lý cuốn chiếu từ trang 1 đến trang cuối..."):
                seq_pages, imgs = process_sequential_mistral_api(mistral_file, st.session_state.saved_mistral_key, "mistral-ocr-latest")
                if seq_pages:
                    st.session_state.active_sequential_pages = seq_pages
                    st.session_state.active_images_dict = imgs
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Hoàn tất xử lý cuốn chiếu thành công!")
                    st.rerun()

with tab2:
    st.subheader("📁 Tải file ZIP kết quả Mistral Offline")
    uploaded_zip = st.file_uploader("Chọn file ZIP kết quả Mistral", type=["zip"], key="offline_zip")
    
    if uploaded_zip:
        seq_pages, imgs = process_sequential_mistral_zip(uploaded_zip.getvalue())
        if seq_pages:
            st.session_state.active_sequential_pages = seq_pages
            st.session_state.active_images_dict = imgs
            st.session_state.active_file_name = uploaded_zip.name.rsplit(".", 1)[0]
            st.success("Đã nạp và xử lý cuốn chiếu từ file ZIP thành công!")
            st.rerun()

if st.session_state.active_sequential_pages:
    st.divider()
    render_sequential_preview(
        st.session_state.active_sequential_pages,
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )