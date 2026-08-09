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
import pypandoc

# Import thư viện google-genai chính thức mới nhất
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

# --- CẤU HÌNH LƯU KEY RA FILE TRÊN SERVER ---
CONFIG_FILE = "config_keys.json"

def load_saved_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_config(gemini_key, mistral_key, mineru_key):
    config_data = {
        "gemini_key": gemini_key,
        "mistral_key": mistral_key,
        "mineru_key": mineru_key
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
    except:
        pass

# Đọc cấu hình đã lưu trên server (nếu có)
saved_config = load_saved_config()

DEFAULT_MINERU_KEY = saved_config.get("mineru_key", "sk-IDb81Oj2W6pHrODooHN0xtKTxEXNzipsnZP6OxAqAl65Kz9O")
DEFAULT_GEMINI_KEY = saved_config.get("gemini_key", "AQ.Ab8RN6IiVh_ufztKik5rSMrl39c-U6_L6v5oy_Qru1-YNUBdRg")
DEFAULT_MISTRAL_KEY = saved_config.get("mistral_key", "Asht2uDLjH8WTWnU06dBWdPbpcVQrbt5")

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title="Convert PDF/Image to word (MinerU - Mistral - Gemini)", page_icon="📐", layout="wide")
MINERU_BASE_URL = "https://mineru.net"

# Tạo thư mục mặc định để lưu file tải về
DEFAULT_DOWNLOAD_DIR = "downloaded_mineru_files"
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
os.makedirs(os.path.join(DEFAULT_DOWNLOAD_DIR, "images"), exist_ok=True)

# --- KHỞI TẠO SESSION STATE ---
if "api_key_editable" not in st.session_state:
    st.session_state.api_key_editable = False
if "gemini_key_editable" not in st.session_state:
    st.session_state.gemini_key_editable = False
if "mistral_key_editable" not in st.session_state:
    st.session_state.mistral_key_editable = False

if "active_json" not in st.session_state:
    st.session_state.active_json = None
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"

# Lưu trữ Key vào Session State
if "saved_gemini_key" not in st.session_state:
    st.session_state.saved_gemini_key = DEFAULT_GEMINI_KEY
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = DEFAULT_MISTRAL_KEY
if "saved_mineru_key" not in st.session_state:
    st.session_state.saved_mineru_key = DEFAULT_MINERU_KEY

# Biến riêng cho Mistral OCR
if "mistral_preview_markdown" not in st.session_state:
    st.session_state.mistral_preview_markdown = ""
if "mistral_docx_bytes" not in st.session_state:
    st.session_state.mistral_docx_bytes = None


# --- 1. CÁC HÀM XỬ LÝ DÙNG CHUNG ---
# --- HÀM DỌN SẠCH FILE CŨ TRONG THƯ MỤC GỐC ---
def cleanup_old_temp_files():
    root_dir = "."
    for f_name in os.listdir(root_dir):
        if f_name.lower().endswith((".jpeg", ".jpg", ".png", ".docx")) or f_name == "temp_input.md":
            try:
                os.remove(os.path.join(root_dir, f_name))
            except:
                pass

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

def get_image_bytes(img_path_str, images_dict, json_upload_dir=""):
    if not img_path_str: return None
    clean_name = os.path.basename(img_path_str)
    
    if images_dict and clean_name in images_dict:
        return io.BytesIO(images_dict[clean_name])
        
    local_img_path = os.path.join("images", clean_name)
    if os.path.exists(local_img_path):
        with open(local_img_path, "rb") as f:
            return io.BytesIO(f.read())
            
    if json_upload_dir:
        auto_path = os.path.join(json_upload_dir, "images", clean_name)
        if os.path.exists(auto_path):
            with open(auto_path, "rb") as f:
                return io.BytesIO(f.read())
                
    if os.path.exists(clean_name):
        with open(clean_name, "rb") as f:
            return io.BytesIO(f.read())
            
    return None

# --- 2. API MINERU & UPLOAD DỰ PHÒNG ---
def upload_temp_file_robust(uploaded_file):
    upload_services = [
        {"name": "Catbox", "url": "https://catbox.moe/user/api.php", "data": {"reqtype": "fileupload"}, "file_key": "fileToUpload"},
        {"name": "Litterbox", "url": "https://litterbox.catbox.moe/resources/api.php", "data": {"reqtype": "fileupload", "time": "24h"}, "file_key": "fileToUpload"},
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
                result_text = res.text.strip()
                if service["name"] == "TmpFiles":
                    try:
                        res_json = res.json()
                        if res_json.get("status") == "success":
                            raw_url = res_json.get("data", {}).get("url", "")
                            if "tmpfiles.org/" in raw_url and not "tmpfiles.org/dl/" in raw_url:
                                raw_url = raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                            return raw_url
                    except:
                        pass
                elif result_text.startswith("http"):
                    return result_text
        except Exception:
            continue
    return None

def start_mineru_task_by_url(api_token, file_url):
    url = f"{MINERU_BASE_URL}/api/v4/extract/task"
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = {"url": file_url, "model_version": "vlm", "is_ocr": True}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("code") == 0:
                return res_json.get("data", {}).get("task_id")
    except: pass
    return None

def check_task_status_v4(api_token, task_id):
    url = f"{MINERU_BASE_URL}/api/v4/extract/task/{task_id}"
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            return res_json.get("data", {})
    except Exception:
        pass
    return {}

# --- 2.1 HÀM XỬ LÝ DỰ PHÒNG BẰNG GEMINI API ---
def fallback_process_with_gemini(uploaded_file, gemini_api_key, selected_model):
    if not GEMINI_AVAILABLE:
        st.error("Chưa cài đặt thư viện `google-genai`.")
        return None, {}
    
    try:
        client = genai.Client(api_key=gemini_api_key)
        file_bytes = uploaded_file.getvalue()
        mime_type = uploaded_file.type
        
        prompt = (
            "Bạn là chuyên gia OCR và chuyển đổi tài liệu toán học. Hãy đọc tài liệu này chính xác tuyệt đối. "
            "Tất cả các biểu thức toán học BẮT BUỘC phải được đặt trong cặp dấu đô la ($...$ cho inline hoặc $$...$$ cho block). "
            "Trình bày kết quả bằng HTML sạch sẽ dùng thẻ <p>, <h3>, bảng dùng <table> có viền rõ ràng. Chỉ trả về HTML hoàn chỉnh."
        )

        response = client.models.generate_content(
            model=selected_model,
            contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt]
        )
        
        html_content = response.text
        html_content = re.sub(r"^```html\s*", "", html_content, flags=re.IGNORECASE)
        html_content = re.sub(r"\s*```$", "", html_content)

        simulated_json = {
            "pdf_info": [{
                "para_blocks": [{
                    "type": "text",
                    "lines": [{"spans": [{"type": "text", "content": html_content}]}]
                }]
            }]
        }
        return simulated_json, {}
    except Exception as e:
        st.error(f"Lỗi khi xử lý bằng Gemini: {e}")
        return None, {}

# --- 3. HÀM QUÉT ĐƯỜNG DẪN ẢNH (MINERU) ---
def collect_image_paths_from_block(block):
    paths = []
    for key in ["image_path", "img_path", "path", "src"]:
        val = block.get(key)
        if val: paths.append(val)
        
    for sub_b in block.get("blocks", []):
        if isinstance(sub_b, dict):
            paths.extend(collect_image_paths_from_block(sub_b))
            
    for line in block.get("lines", []):
        if isinstance(line, dict):
            for span in line.get("spans", []):
                if isinstance(span, dict):
                    for key in ["image_path", "img_path", "path", "src"]:
                        val = span.get(key)
                        if val: paths.append(val)
    return paths

# --- 4. RENDER PREVIEW CHO MINERU / GEMINI ---
def render_pure_math_preview(json_data, images_dict, json_upload_dir="", file_name="document"):
    preview_inner_html = '<div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">'
    
    pages = []
    if isinstance(json_data, list): pages = json_data
    elif isinstance(json_data, dict):
        pages = json_data.get("pdf_info", [])
        if not pages and "para_blocks" in json_data: pages = [json_data]

    for page in pages:
        if not isinstance(page, dict): continue
        blocks = page.get("para_blocks", page.get("blocks", []))
        for block in blocks:
            if not isinstance(block, dict): continue
            b_type = block.get("type")
            
            if b_type in ["text", "title", "paragraph", "header", "footer"]:
                p_text = ""
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        span_type = span.get("type")
                        content = span.get("content", span.get("text", ""))
                        if span_type == "text" or not span_type:
                            if re.match(r"^Bài\s+\d+", content.strip()):
                                p_text += f"<b>{content}</b>"
                            else:
                                p_text += content
                        elif span_type in ["inline_equation", "equation", "math"]:
                            p_text += f" {clean_and_wrap_latex(content)} "
                if p_text.strip():
                    if "HƯỚNG DẪN CHẤM" in p_text or "Đáp án" in p_text:
                        preview_inner_html += f"<h3 style='color: #2b6cb0; margin-top: 20px;'>{p_text}</h3>"
                    else:
                        preview_inner_html += f"<p style='margin-bottom: 10px;'>{p_text}</p>"

            elif b_type in ["image", "chart", "figure"]:
                all_img_paths = collect_image_paths_from_block(block)
                for img_path_str in set(all_img_paths):
                    img_stream = get_image_bytes(img_path_str, images_dict, json_upload_dir)
                    if img_stream:
                        encoded = base64.b64encode(img_stream.getvalue()).decode("utf-8")
                        preview_inner_html += f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/png;base64,{encoded}" style="max-width: 450px; border-radius: 8px; border: 1px solid #2d3748;" /></div>'

            elif b_type == "table":
                for sub_b in block.get("blocks", [block]):
                    for line in sub_b.get("lines", []):
                        for span in line.get("spans", []):
                            table_html = span.get("html", span.get("table_html", ""))
                            if table_html:
                                soup = BeautifulSoup(table_html, "html.parser")
                                for table_tag in soup.find_all("table"):
                                    table_tag['style'] = "border-collapse: collapse; width: auto; max-width: 100%; margin: 15px auto; border: 2px solid #2d3748;"
                                for th_tag in soup.find_all("th"):
                                    th_tag['style'] = "border: 2px solid #2d3748; padding: 6px 10px; background-color: #edf2f7; font-weight: bold; text-align: center;"
                                for td_tag in soup.find_all("td"):
                                    td_tag['style'] = "border: 2px solid #2d3748; padding: 6px 10px; vertical-align: middle;"
                                for eq_tag in soup.find_all("eq"):
                                    eq_tag.string = clean_and_wrap_latex(eq_tag.get_text())
                                preview_inner_html += f"<div style='margin: 15px 0; overflow-x: auto;'>{str(soup)}</div>"

    preview_inner_html += '</div>'
    
    copier_component = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {{delimiters: [{{left: '$', right: '$', display: false}}]}});"></script>
        <script src="https://cdn.jsdelivr.net/npm/html-docx-js@0.3.1/dist/html-docx.js"></script>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 10px; background-color: #ffffff; }}
            .btn-action {{ padding: 10px 20px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 10px; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .btn-copy {{ background-color: #2b6cb0; }}
            .btn-word {{ background-color: #2f855a; }}
            .preview-card {{ background-color: #ffffff; padding: 30px; border-radius: 10px; border: 1px solid #cbd5e0; max-height: 600px; overflow-y: auto; }}
        </style>
    </head>
    <body>
        <div>
            <button class="btn-action btn-copy" onclick="copyContentToClipboard()">📋 Sao chép nhanh (Dán vào Word)</button>
            <button class="btn-action btn-word" onclick="saveAsWordDocx()">💾 Lưu thành file Word (.docx)</button>
            <span id="status-msg" style="margin-left: 10px; color: #2f855a; font-weight: bold; display: none;">✔ Thành công!</span>
        </div>
        <div class="preview-card" id="preview-box">{preview_inner_html}</div>
        <script>
        function copyContentToClipboard() {{
            const range = document.createRange();
            range.selectNode(document.getElementById('content-to-copy'));
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            try {{ document.execCommand('copy'); }} catch (err) {{}}
            window.getSelection().removeAllRanges();
            const status = document.getElementById('status-msg');
            status.innerText = "✔ Đã sao chép!";
            status.style.display = 'inline';
            setTimeout(() => {{ status.style.display = 'none'; }}, 3000);
        }}
        function saveAsWordDocx() {{
            const contentHTML = document.getElementById('preview-box').innerHTML;
            const converted = htmlDocx.asBlob('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + contentHTML + '</body></html>');
            const link = document.createElement('a');
            link.href = URL.createObjectURL(converted);
            link.download = "{file_name}.docx";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }}
        </script>
    </body>
    </html>
    """
    st.markdown("### 👁️ Bản xem trước Nội dung MinerU / Gemini")
    components.html(copier_component, height=750, scrolling=False)

# --- 5. GIAO DIỆN CHÍNH (4 TABS) ---
st.title("📐 Convert PDF/Image to word (MinerU - Mistral - Gemini)")

tab1, tab2, tab3, tab4 = st.tabs([
    "🚀 Gửi lên MinerU Server (API)", 
    "🌪️ Mistral OCR (API + Pandoc)",
    "🌐 MinerU Web Extractor", 
    "📁 Tải file có sẵn (Offline)"
])

# ==========================================
# TAB 1: MINERU SERVER & GEMINI
# ==========================================
with tab1:
    st.subheader("Cấu hình API Keys (MinerU & Gemini dự phòng)")
    col_k1, col_k2 = st.columns(2)
    with col_k1:
        api_token_input = st.text_input("Nhập MinerU API Token:", value=st.session_state.saved_mineru_key, type="password", disabled=not st.session_state.api_key_editable)
        if st.button("Đổi MinerU Key"):
            st.session_state.api_key_editable = not st.session_state.api_key_editable
            st.rerun()
        if st.session_state.api_key_editable and st.button("Lưu MinerU Key"):
            st.session_state.saved_mineru_key = api_token_input
            save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)
            st.session_state.api_key_editable = False
            st.success("Đã lưu MinerU Key vào server!")
            st.rerun()
            
    with col_k2:
        def update_gemini_key():
            st.session_state.saved_gemini_key = st.session_state.gemini_input_field
            save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)

        gemini_token_input = st.text_input("Nhập Gemini API Key (Dự phòng):", value=st.session_state.saved_gemini_key, type="password", disabled=not st.session_state.gemini_key_editable, key="gemini_input_field", on_change=update_gemini_key)
        if st.button("Đổi Gemini Key"):
            st.session_state.gemini_key_editable = not st.session_state.gemini_key_editable
            st.rerun()
        if st.session_state.gemini_key_editable and st.button("Lưu Gemini Key"):
            st.session_state.saved_gemini_key = gemini_token_input
            save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)
            st.session_state.gemini_key_editable = False
            st.success("Đã lưu Gemini Key vào server!")
            st.rerun()

    selected_gemini_model = st.selectbox("Chọn Model Gemini dự phòng:", ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"], index=0)
    api_file = st.file_uploader("Chọn file PDF hoặc ảnh cần phân tích qua MinerU", type=["pdf", "png", "jpg", "jpeg"], key="tab1_upload")
    
    if st.button("📤 Gửi & Phân tích qua MinerU"):
        if not api_file:
            st.warning("Vui lòng chọn file!")
        else:
            success_processed = False
            with st.spinner("Đang tải file lên máy chủ trung gian..."):
                file_url = upload_temp_file_robust(api_file)
                
            if file_url:
                with st.spinner("Đang khởi tạo tác vụ xử lý trên MinerU..."):
                    task_id = start_mineru_task_by_url(st.session_state.saved_mineru_key, file_url)
                if task_id:
                    st.success(f"Khởi tạo thành công! Task ID: `{task_id}`")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    for i in range(40):
                        time.sleep(3)
                        task_data = check_task_status_v4(st.session_state.saved_mineru_key, task_id)
                        state = task_data.get("state")
                        status_text.text(f"Trạng thái MinerU: {state}")
                        if state == "done":
                            full_zip_url = task_data.get("full_zip_url")
                            r_zip = requests.get(full_zip_url)
                            if r_zip.status_code == 200:
                                found_json, images_dict = extract_zip_and_get_data(r_zip.content)
                                if found_json:
                                    st.session_state.active_json = found_json
                                    st.session_state.active_images_dict = images_dict
                                    st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                                    st.success("Đã hoàn tất phân tích bằng MinerU!")
                                    success_processed = True
                                    st.rerun()
                            break
                        elif state == "failed":
                            break
                        progress_bar.progress(min((i + 1) * 2, 100))

            if not success_processed:
                active_key = st.session_state.saved_gemini_key.strip()
                if active_key:
                    with st.spinner(f"MinerU gặp sự cố. Đang tiến hành trích xuất dự phòng bằng {selected_gemini_model}..."):
                        g_json, g_imgs = fallback_process_with_gemini(api_file, active_key, selected_gemini_model)
                        if g_json:
                            st.session_state.active_json = g_json
                            st.session_state.active_images_dict = g_imgs
                            st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                            st.success(f"Đã hoàn tất trích xuất thay thế bằng {selected_gemini_model}!")
                            st.rerun()
                else:
                    st.error("MinerU thất bại và chưa có Gemini API Key dự phòng!")


# ==========================================
# TAB 2: MISTRAL OCR (GIỮ TÊN GỐC & GOM ẢNH TOÀN DIỆN TỪ MỌI THƯ MỤC)
# ==========================================
with tab2:
    st.subheader("🌪️ Cấu hình Mistral OCR & Pandoc")
    def update_mistral_key():
        st.session_state.saved_mistral_key = st.session_state.mistral_input_field
        save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)

    mistral_token_input = st.text_input("Nhập Mistral API Key:", value=st.session_state.saved_mistral_key, type="password", disabled=not st.session_state.mistral_key_editable, key="mistral_input_field", on_change=update_mistral_key)
    if st.button("Đổi Mistral Key"):
        st.session_state.mistral_key_editable = not st.session_state.mistral_key_editable
        st.rerun()
    if st.session_state.mistral_key_editable and st.button("Lưu Mistral Key"):
        st.session_state.saved_mistral_key = mistral_token_input
        save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)
        st.session_state.mistral_key_editable = False
        st.success("Đã lưu Mistral Key vào server!")
        st.rerun()

    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="mistral_upload")
    
    if st.button("🚀 Gửi PDF lên Mistral OCR & Phân tích"):
        active_m_key = st.session_state.saved_mistral_key.strip()
        if not mistral_file:
            st.warning("Vui lòng chọn file!")
        elif not active_m_key:
            st.error("Vui lòng nhập Mistral API Key!")
        elif not MISTRAL_AVAILABLE:
            st.error("Chưa cài đặt thư viện `mistralai`.")
        else:
            original_full_name = mistral_file.name
            base_name_only = original_full_name.rsplit('.', 1)[0]
            
            with st.spinner(f"Đang xử lý file '{original_full_name}', dọn file cũ, gom ảnh và tạo file Word..."):
                try:
                    root_dir = "."
                    
                    # 1. DỌN SẠCH CÁC FILE ẢNH CŨ VÀ FILE TẠM TRONG THƯ MỤC GỐC
                    cleanup_old_temp_files()

                    client = Mistral(api_key=active_m_key)
                    file_bytes = mistral_file.getvalue()
                    base64_file = base64.b64encode(file_bytes).decode('utf-8')

                    ocr_response = client.ocr.process(
                        document={"type": "document_url", "document_url": f"data:application/pdf;base64,{base64_file}"},
                        model="mistral-ocr-latest",
                        include_image_base64=True,
                        include_blocks=True
                    )
                    
                    full_markdown = ""
                    images_dict = {}
                    
                    if hasattr(ocr_response, "pages"):
                        for idx, page in enumerate(ocr_response.pages):
                            page_md = page.markdown if hasattr(page, "markdown") else ""
                            # Chuẩn hóa cú pháp đường dẫn ảnh thành tên file thuần túy để Pandoc nhận diện ở thư mục gốc
                            page_md = re.sub(r'!\[(.*?)\]\([^)]*?(img_[^)]+?\.(?:jpeg|jpg|png))\)', r'![\1](\2)', page_md)
                            page_md_safe = re.sub(r'^\s*---\s*$', '<hr/>', page_md, flags=re.MULTILINE)
                            full_markdown += f"\n\n<hr/>\n<h3>Trang {idx+1}</h3>\n\n" + page_md_safe
                            
                            # Lưu trữ bytes ảnh nhận được từ API vào thư mục gốc
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
                                            with open(os.path.join(root_dir, img_filename), "wb") as img_f:
                                                img_f.write(img_bytes)
                                        except: 
                                            pass

                    # 2. QUÉT TOÀN BỘ CÁC THƯ MỤC CON (page-1 đến page-163) ĐỂ GOM TOÀN BỘ ẢNH RA THƯ MỤC GỐC
                    for item in os.listdir(root_dir):
                        item_path = os.path.join(root_dir, item)
                        if os.path.isdir(item_path):
                            for sub_f in os.listdir(item_path):
                                if sub_f.lower().endswith((".jpeg", ".jpg", ".png")):
                                    src_img = os.path.join(item_path, sub_f)
                                    dst_img = os.path.join(root_dir, sub_f)
                                    try:
                                        with open(src_img, "rb") as sf, open(dst_img, "wb") as df:
                                            df.write(sf.read())
                                        with open(src_img, "rb") as sf:
                                            images_dict[sub_f] = sf.read()
                                    except:
                                        pass

                    # 3. BIÊN DỊCH FILE WORD BẰNG PANDOC VỚI THAM SỐ RESOURCE-PATH
                    temp_md_path = "temp_input.md"
                    with open(temp_md_path, "w", encoding="utf-8") as f:
                        f.write(full_markdown)
                        
                    output_docx = f"{base_name_only}.docx"
                    pypandoc.convert_file(
                        temp_md_path, 
                        'docx', 
                        outputfile=output_docx, 
                        extra_args=['--standalone', f'--resource-path={root_dir}']
                    )
                    
                    with open(output_docx, "rb") as f:
                        docx_bytes = f.read()

                    st.session_state.mistral_preview_markdown = full_markdown
                    st.session_state.active_images_dict = images_dict
                    st.session_state.mistral_docx_bytes = docx_bytes
                    st.session_state.active_file_name = base_name_only
                    
                    if os.path.exists(temp_md_path): os.remove(temp_md_path)
                    st.success(f"🎉 Xử lý thành công file `{original_full_name}`, đã gom toàn bộ ảnh và tạo file Word chuẩn Pandoc!")
                except Exception as e:
                    st.error(f"Lỗi Mistral OCR: {e}")

    # Hiển thị Khung Preview HTML và các nút tải xuống giữ nguyên tên file gốc
    if st.session_state.mistral_preview_markdown:
        st.divider()
        
        current_file_name = st.session_state.get("active_file_name", "Document")
        
        col_m1, col_m2 = st.columns([2, 1])
        with col_m1:
            st.subheader(f"👁️ Bản xem trước: {current_file_name}")
        with col_m2:
            if st.session_state.get("mistral_docx_bytes"):
                st.download_button(
                    label=f"📥 Tải Word: {current_file_name}.docx",
                    data=st.session_state.mistral_docx_bytes,
                    file_name=f"{current_file_name}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True
                )

        raw_md = st.session_state.mistral_preview_markdown
        
        def replace_img_smart_html(match):
            alt_text = match.group(1)
            raw_path = match.group(2)
            target_name = os.path.basename(raw_path)
            matched_bytes = None
            for k, v in st.session_state.active_images_dict.items():
                if target_name in k or k in target_name:
                    matched_bytes = v
                    break
            if matched_bytes:
                b64_data = base64.b64encode(matched_bytes).decode('utf-8')
                return f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{b64_data}" style="max-width: 450px; border-radius: 8px; border: 1px solid #2d3748;" alt="{alt_text}" /></div>'
            return match.group(0)

        processed_html = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_img_smart_html, raw_md)
        escaped_markdown_json = json.dumps(processed_html)

        mistral_component_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/html-docx-js@0.3.1/dist/html-docx.js"></script>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 10px; background-color: #ffffff; color: #2d3748; }}
                .btn-action {{ padding: 10px 20px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 10px; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
                .btn-copy {{ background-color: #2b6cb0; }}
                .btn-copy:hover {{ background-color: #2c5282; }}
                .btn-word {{ background-color: #2f855a; }}
                .btn-word:hover {{ background-color: #276749; }}
                #status-msg {{ margin-left: 10px; color: #2f855a; font-weight: bold; font-size: 13px; display: none; }}
                .preview-card {{ background-color: #ffffff; padding: 30px; border-radius: 10px; border: 1px solid #cbd5e0; max-height: 600px; overflow-y: auto; line-height: 1.8; }}
                table {{ border-collapse: collapse; width: auto; max-width: 100%; margin: 15px auto; border: 2px solid #2d3748; }}
                th {{ border: 2px solid #2d3748; padding: 6px 10px; background-color: #edf2f7; font-weight: bold; text-align: center; }}
                td {{ border: 2px solid #2d3748; padding: 6px 10px; vertical-align: middle; }}
            </style>
        </head>
        <body>
            <div>
                <button class="btn-action btn-copy" onclick="copyContentToClipboard()">📋 Sao chép nhanh (Dán vào Word)</button>
                <button class="btn-action btn-word" onclick="saveAsWordDocx()">💾 Tải nhanh file Word (.docx)</button>
                <span id="status-msg">✔ Thao tác thành công!</span>
            </div>
            <div class="preview-card" id="content-to-copy"></div>

            <script>
            const rawMarkdown = {escaped_markdown_json};
            document.getElementById('content-to-copy').innerHTML = marked.parse(rawMarkdown);

            function renderMath() {{
                if (typeof renderMathInElement === 'function') {{
                    renderMathInElement(document.getElementById('content-to-copy'), {{
                        delimiters: [
                            {{left: '$$', right: '$$', display: true}},
                            {{left: '$', right: '$', display: false}},
                            {{left: '\\\\[', right: '\\\\]', display: true}},
                            {{left: '\\\\(', right: '\\\\)', display: false}}
                        ],
                        throwOnError: false
                    }});
                }}
            }}

            document.addEventListener("DOMContentLoaded", renderMath);
            setTimeout(renderMath, 300);

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
                if (typeof htmlDocx === 'undefined') {{
                    alert('Thư viện htmlDocx chưa sẵn sàng, vui lòng thử lại!');
                    return;
                }}
                try {{
                    const converted = htmlDocx.asBlob('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + contentHTML + '</body></html>');
                    const link = document.createElement('a');
                    link.href = URL.createObjectURL(converted);
                    link.download = "{current_file_name}.docx";
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    showStatus("Đã tải xuống thành công!");
                }} catch (err) {{
                    alert('Lỗi xuất file: ' + err.message);
                }}
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
        components.html(mistral_component_html, height=780, scrolling=False)


# ==========================================
# TAB 3: MINERU WEB EXTRACTOR
# ==========================================
with tab3:
    st.subheader("🌐 MinerU Web Extractor (Nhúng trực tiếp)")
    st.markdown("[🔗 Mở trang MinerU Web Extractor trong tab mới](https://mineru.net/OpenSourceTools/Extractor)", unsafe_allow_html=True)
    components.iframe("https://mineru.net/OpenSourceTools/Extractor", height=650, scrolling=True)
    
    st.divider()
    st.subheader("📥 Nạp file kết quả từ Web Extractor")
    web_json_f = st.file_uploader("Tải file layout.json từ gói kết quả Web", type=["json"], key="web_json_tab3")
    web_image_files = st.file_uploader("Tải toàn bộ file ảnh trong thư mục images", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="web_imgs_tab3")
    
    if web_json_f:
        try:
            json_bytes = web_json_f.getvalue()
            st.session_state.active_json = json.loads(json_bytes.decode("utf-8"))
            st.session_state.active_file_name = web_json_f.name.rsplit(".", 1)[0]
            if web_image_files:
                st.session_state.active_images_dict = {img.name: img.getvalue() for img in web_image_files}
            st.success("Đã nạp dữ liệu thành công từ Web Extractor!")
            st.rerun()
        except Exception as e:
            st.error(f"Lỗi: {e}")


# ==========================================
# TAB 4: TẢI FILE CÓ SẴN (OFFLINE)
# ==========================================
with tab4:
    st.subheader("📁 Nạp file layout.json hoặc file ZIP kết quả Offline")
    offline_file = st.file_uploader("Chọn file layout.json hoặc file ZIP kết quả", type=["json", "zip"], key="offline_all")
    image_files = st.file_uploader("Chọn các file ảnh liên quan (nếu dùng layout.json)", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="offline_imgs_all")
    
    if offline_file:
        try:
            file_bytes = offline_file.getvalue()
            if offline_file.name.endswith(".zip"):
                found_json, images_dict = extract_zip_and_get_data(file_bytes)
                if found_json:
                    st.session_state.active_json = found_json
                    st.session_state.active_images_dict = images_dict
                    st.session_state.active_file_name = offline_file.name.rsplit(".", 1)[0]
                    st.success("Đã nạp file ZIP thành công!")
                    st.rerun()
            elif offline_file.name.endswith(".json"):
                st.session_state.active_json = json.loads(file_bytes.decode("utf-8"))
                st.session_state.active_file_name = offline_file.name.rsplit(".", 1)[0]
                if image_files:
                    st.session_state.active_images_dict = {img.name: img.getvalue() for img in image_files}
                st.success("Đã nạp file layout.json thành công!")
                st.rerun()
        except Exception as e:
            st.error(f"Lỗi khi đọc file: {e}")


# ==========================================
# HIỂN THỊ PREVIEW CHO MINERU / GEMINI
# ==========================================
if st.session_state.active_json is not None:
    st.divider()
    render_pure_math_preview(
        st.session_state.active_json, 
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )