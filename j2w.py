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

# Import thư viện google-genai mới nhất
try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Import thư viện mistralai SDK 2.0
try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title="Convert PDF/Image to word", page_icon="📐", layout="wide")
MINERU_BASE_URL = "https://mineru.net"
DEFAULT_API_KEY = "sk-IDb81Oj2W6pHrODooHN0xtKTxEXNzipsnZP6OxAqAl65Kz9O"

DEFAULT_DOWNLOAD_DIR = "downloaded_mineru_files"
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
os.makedirs(os.path.join(DEFAULT_DOWNLOAD_DIR, "images"), exist_ok=True)

# --- KHỞI TẠO SESSION STATE ---
if "api_key_editable" not in st.session_state:
    st.session_state.api_key_editable = False
if "gemini_key_editable" not in st.session_state:
    st.session_state.gemini_key_editable = False
if "active_json" not in st.session_state:
    st.session_state.active_json = None
if "active_mistral_pages" not in st.session_state:
    st.session_state.active_mistral_pages = [] # Danh sách chứa dữ liệu từng trang gồm markdown + metadata + images
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"

if "saved_gemini_key" not in st.session_state:
    st.session_state.saved_gemini_key = ""
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = ""

# --- 1. CÁC HÀM XỬ LÝ DÙNG CHUNG ---

def clean_and_wrap_latex(latex_str):
    if not latex_str: return ""
    clean_str = latex_str.strip()
    if clean_str.startswith("$") and clean_str.endswith("$"):
        clean_str = clean_str[1:-1].strip()
    return f"${clean_str}$"

def extract_zip_and_get_data(zip_bytes):
    images_dict = {}
    json_data = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for filename in z.namelist():
            if "images/" in filename and not filename.endswith("/"):
                img_name = os.path.basename(filename)
                images_dict[img_name] = z.read(filename)
            elif filename.endswith("layout.json") and not filename.startswith("__MACOSX"):
                try:
                    json_data = json.loads(z.read(filename).decode("utf-8"))
                except Exception:
                    pass
    return json_data, images_dict

# --- HÀM PHÂN TÍCH CHUYÊN SÂU CẤU TRÚC MISTRAL ZIP (PAGE-X & METADATA) ---
def extract_mistral_zip_advanced(zip_bytes):
    images_dict = {}
    pages_data = []
    
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        
        # Tìm tất cả các thư mục trang page-X trong file ZIP
        page_dirs = set()
        for f in namelist:
            match = re.search(r"pages/(page-\d+)/", f)
            if match:
                page_dirs.add(match.group(1))
        
        # Sắp xếp thứ tự trang page-1, page-2, ... theo số thứ tự trang
        sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
        
        for p_dir in sorted_pages:
            page_info = {"page_name": p_dir, "markdown": "", "metadata": {}, "images": {}}
            
            md_path = f"pages/{p_dir}/markdown.md"
            meta_path = f"pages/{p_dir}/page-metadata.json"
            
            if md_path in namelist:
                try:
                    page_info["markdown"] = z.read(md_path).decode("utf-8")
                except: pass
                
            if meta_path in namelist:
                try:
                    page_info["metadata"] = json.loads(z.read(meta_path).decode("utf-8"))
                except: pass
                
            # Trích xuất ảnh thuộc riêng trang này
            for f in namelist:
                if f.startswith(f"pages/{p_dir}/") and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                    img_name = os.path.basename(f)
                    img_bytes = z.read(f)
                    page_info["images"][img_name] = img_bytes
                    images_dict[img_name] = img_bytes
                    
            pages_data.append(page_info)
            
    return pages_data, images_dict

def get_image_bytes(img_path_str, images_dict, json_upload_dir=""):
    if not img_path_str: return None
    clean_name = os.path.basename(img_path_str)
    if images_dict and clean_name in images_dict:
        return io.BytesIO(images_dict[clean_name])
    return None

# --- 2. API MINERU & MISTRAL OCR ---

def upload_temp_file_robust(uploaded_file):
    upload_services = [
        {"name": "Catbox", "url": "https://catbox.moe/user/api.php", "data": {"reqtype": "fileupload"}, "file_key": "fileToUpload"},
        {"name": "TmpFiles", "url": "https://tmpfiles.org/api/v1/upload", "data": {}, "file_key": "file"}
    ]
    file_bytes = uploaded_file.getvalue()
    file_name = uploaded_file.name
    file_type = uploaded_file.type

    for service in upload_services:
        try:
            files = {service["file_key"]: (file_name, file_bytes, file_type)}
            res = requests.post(service["url"], data=service["data"], files=files, timeout=30)
            if res.status_code == 200:
                if service["name"] == "TmpFiles":
                    res_json = res.json()
                    if res_json.get("status") == "success":
                        return res_json.get("data", {}).get("url", "").replace("tmpfiles.org/", "tmpfiles.org/dl/")
                elif res.text.strip().startswith("http"):
                    return res.text.strip()
        except: continue
    return None

def start_mineru_task_by_url(api_token, file_url):
    url = f"{MINERU_BASE_URL}/api/v4/extract/task"
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = {"url": file_url, "model_version": "vlm", "is_ocr": True}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("code") == 0: return res_json.get("data", {}).get("task_id")
    except: pass
    return None

def check_task_status_v4(api_token, task_id):
    url = f"{MINERU_BASE_URL}/api/v4/extract/task/{task_id}"
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200: return response.json().get("data", {})
    except: pass
    return {}

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
                page_info = {"page_name": p_dir, "markdown": "", "metadata": {}, "images": {}}
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

# --- 3. RENDER PREVIEW CHUYÊN BIỆT CHO MISTRAL (XỬ LÝ METADATA & EQUATION) ---
def render_mistral_advanced_preview(pages_data, file_name="Document"):
    combined_html = '<div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">'
    
    for page in pages_data:
        p_name = page["page_name"]
        md_text = page["markdown"]
        metadata = page["metadata"]
        images = page["images"]
        
        combined_html += f"<div style='border-bottom: 2px dashed #cbd5e0; margin-bottom: 25px; padding-bottom: 15px;'>"
        combined_html += f"<h4 style='color: #4a5568;'>📄 Trang {p_name.split('-')[1]}</h4>"
        
        # Phân tích blocks từ metadata nếu có để tối ưu hóa hiển thị Equation / Image chính xác theo tọa độ
        blocks = metadata.get("blocks", [])
        if blocks:
            for block in blocks:
                b_type = block.get("type")
                content = block.get("content", "")
                
                if b_type == "image":
                    img_id = block.get("imageId")
                    if img_id and img_id in images:
                        encoded = base64.b64encode(images[img_id]).decode("utf-8")
                        combined_html += f'<div style="text-align: center; margin: 15px 0;"><img src="data:image/jpeg;base64,{encoded}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /><p style="font-size: 12px; color: #718096;">[Hình ảnh từ {p_name}]</p></div>'
                elif b_type in ["header", "footer"]:
                    combined_html += f"<div style='font-size: 13px; color: #a0aec0; margin: 5px 0;'>{content}</div>"
                else:
                    # Render nội dung text/markdown thông thường
                    formatted_content = content
                    # Đảm bảo các công thức toán học được bọc đúng chuẩn toán học LaTeX
                    combined_html += f"<p>{formatted_content}</p>"
        else:
            # Fallback nếu không có metadata, render trực tiếp markdown của trang
            for img_name, img_bytes in images.items():
                if img_name in md_text:
                    encoded = base64.b64encode(img_bytes).decode("utf-8")
                    md_text = md_text.replace(f"({img_name})", f"(data:image/jpeg;base64,{encoded})")
            
            # Chuẩn hóa cú pháp toán học thô thành định dạng hiển thị công thức Equation LaTeX
            md_text = re.sub(r'(\$.*?\$)', r'<span class="math-tex">\1</span>', md_text)
            combined_html += f"<div>{md_text}</div>"
            
        combined_html += "</div>"
        
    combined_html += '</div>'

    preview_component = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {{delimiters: [{{left: '$', right: '$', display: false}}, {{left: '$$', right: '$$', display: true}}]}});"></script>
        <script src="https://cdn.jsdelivr.net/npm/html-docx-js@0.3.1/dist/html-docx.js"></script>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 15px; background: #fff; color: #333; }}
            .btn-action {{ padding: 10px 20px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 10px; margin-bottom: 15px; }}
            .btn-copy {{ background-color: #2b6cb0; }}
            .btn-word {{ background-color: #2f855a; }}
            .preview-card {{ padding: 30px; border: 1px solid #cbd5e0; border-radius: 8px; max-height: 650px; overflow-y: auto; background: #fff; }}
            img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; }}
        </style>
    </head>
    <body>
        <div>
            <button class="btn-action btn-copy" onclick="copyContent()">📋 Sao chép nhanh (Dán vào Word)</button>
            <button class="btn-action btn-word" onclick="saveWord()">💾 Tải file Word (.docx)</button>
        </div>
        <div class="preview-card" id="preview-box">
            {combined_html}
        </div>
        <script>
            function copyContent() {{
                const range = document.createRange();
                range.selectNode(document.getElementById('content-to-copy'));
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                document.execCommand('copy');
                window.getSelection().removeAllRanges();
                alert('Đã sao chép vào bộ nhớ tạm!');
            }}

            function saveWord() {{
                const html = document.getElementById('content-to-copy').innerHTML;
                const converted = htmlDocx.asBlob('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + html + '</body></html>');
                const link = document.createElement('a');
                link.href = URL.createObjectURL(converted);
                link.download = "{file_name}_Mistral_Advanced.docx";
                link.click();
            }}
        </script>
    </body>
    </html>
    """
    st.markdown("### 👁️ Bản xem trước nâng cao (Phân tích Metadata & Markdown theo từng trang)")
    components.html(preview_component, height=780, scrolling=False)


# --- 4. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2, tab3 = st.tabs([
    "🌪️ Mistral OCR Server (Advanced)", 
    "🌐 MinerU Web Extractor", 
    "📁 Tải file kết quả ZIP (Offline)"
])

with tab1:
    st.subheader("🌪️ Cấu hình Mistral OCR (Phân tích theo Page-X)")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="t2")
    
    if st.button("📤 Gửi & Xử lý nâng cao qua Mistral", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang phân tích cấu trúc từng trang qua Mistral OCR..."):
                pages_data = process_with_mistral(mistral_file, st.session_state.saved_mistral_key, "mistral-ocr-latest")
                if pages_data:
                    st.session_state.active_mistral_pages = pages_data
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Hoàn tất phân tích nâng cao cấu trúc các trang!")
                    st.rerun()

with tab2:
    st.subheader("🌐 MinerU Web Extractor")
    components.iframe("https://mineru.net/OpenSourceTools/Extractor", height=650, scrolling=True)

with tab3:
    st.subheader("📁 Tải file kết quả ZIP Mistral Offline (Chứa thư mục pages)")
    uploaded_zip = st.file_uploader("Chọn file ZIP kết quả Mistral", type=["zip"], key="offline_zip")
    
    if uploaded_zip:
        pages_data, _ = extract_mistral_zip_advanced(uploaded_zip.getvalue())
        if pages_data:
            st.session_state.active_mistral_pages = pages_data
            st.session_state.active_file_name = uploaded_zip.name.rsplit(".", 1)[0]
            st.success("Đã nạp và giải mã cấu trúc thư mục pages thành công!")
            st.rerun()

# Điều phối hiển thị Preview nâng cao
if st.session_state.active_mistral_pages:
    st.divider()
    render_mistral_advanced_preview(
        st.session_state.active_mistral_pages,
        file_name=st.session_state.active_file_name
    )