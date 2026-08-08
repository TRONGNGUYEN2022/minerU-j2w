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

# Import thư viện google-genai chính thức mới nhất
try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Import thư viện Mistral chính thức
try:
    from mistralai import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title="Convert PDF/Image to word", page_icon="📐", layout="wide")
MINERU_BASE_URL = "https://mineru.net"
DEFAULT_API_KEY = "sk-IDb81Oj2W6pHrODooHN0xtKTxEXNzipsnZP6OxAqAl65Kz9O"

# Tạo thư mục mặc định để lưu file tải về từ MinerU Web Extractor
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
    st.session_state.active_file_name = "MinerU_Document"

# Khởi tạo lưu trữ API Keys tự động trong session
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
        {
            "name": "Catbox",
            "url": "https://catbox.moe/user/api.php",
            "data": {"reqtype": "fileupload"},
            "file_key": "fileToUpload"
        },
        {
            "name": "Litterbox",
            "url": "https://litterbox.catbox.moe/resources/api.php",
            "data": {"reqtype": "fileupload", "time": "24h"},
            "file_key": "fileToUpload"
        },
        {
            "name": "TmpFiles",
            "url": "https://tmpfiles.org/api/v1/upload",
            "data": {},
            "file_key": "file"
        }
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
            data = res_json.get("data", {})
            if data.get("state") == "failed":
                print(f"DEBUG MINERU ERROR RAW: {res_json}")
            return data
    except Exception as e:
        print(f"Lỗi kiểm tra task: {e}")
    return {}


# --- 2.1 HÀM XỬ LÝ DỰ PHÒNG BẰNG GEMINI API ---
def fallback_process_with_gemini(uploaded_file, gemini_api_key, selected_model):
    if not GEMINI_AVAILABLE:
        st.error("Chưa cài đặt thư viện `google-genai`. Vui lòng thêm `google-genai` vào requirements.txt")
        return None, {}
    
    try:
        client = genai.Client(api_key=gemini_api_key)
        file_bytes = uploaded_file.getvalue()
        mime_type = uploaded_file.type
        
        prompt = (
            "Bạn là một chuyên gia OCR và chuyển đổi tài liệu toán học. Hãy đọc tài liệu này thật chính xác tuyệt đối. "
            "Yêu cầu cực kỳ quan trọng đối với công thức toán học: Tất cả các biểu thức toán học, phân số, số mũ, ký hiệu (như \\frac, \\pm, \\cdot, v.v.) BẮT BUỘC phải được đặt trong cặp dấu đô la ($...$ cho công thức inline hoặc $$...$$ cho công thức đứng dòng riêng biệt). Không được để mã LaTeX trần trụi ngoài văn bản. "
            "Trình bày kết quả cấu trúc thành một đoạn mã HTML sạch sẽ, trong đó các đoạn văn dùng thẻ <p>, tiêu đề dùng thẻ <h3> hoặc <b>, bảng biểu dùng thẻ <table> được đóng khung viền rõ ràng. "
            "Chỉ trả về nội dung HTML hoàn chỉnh, không kèm theo giải thích gì thêm."
        )

        response = client.models.generate_content(
            model=selected_model,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                prompt
            ]
        )
        
        html_content = response.text
        html_content = re.sub(r"^```html\s*", "", html_content, flags=re.IGNORECASE)
        html_content = re.sub(r"\s*```$", "", html_content)

        simulated_json = {
            "pdf_info": [
                {
                    "para_blocks": [
                        {
                            "type": "text",
                            "lines": [
                                {
                                    "spans": [
                                        {"type": "text", "content": html_content}
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        return simulated_json, {}
    except Exception as e:
        st.error(f"Lỗi khi xử lý dự phòng bằng Gemini: {e}")
        return None, {}


# --- 2.2 HÀM XỬ LÝ BẰNG MISTRAL OCR API ---
def process_with_mistral(uploaded_file, mistral_api_key, selected_model):
    if not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai`. Vui lòng thêm `mistralai` vào requirements.txt")
        return None, {}
    
    try:
        client = Mistral(api_key=mistral_api_key)
        file_bytes = uploaded_file.getvalue()
        file_name = uploaded_file.name
        
        # Mã hóa base64 để truyền dữ liệu file trực tiếp lên Mistral OCR
        base64_data = base64.b64encode(file_bytes).decode("utf-8")
        file_ext = file_name.split(".")[-1].lower()
        
        if file_ext == "pdf":
            document_payload = {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{base64_data}"
            }
        else:
            mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "avif": "image/avif"}
            mime_type = mime_map.get(file_ext, "image/jpeg")
            document_payload = {
                "type": "image_url",
                "image_url": f"data:{mime_type};base64,{base64_data}"
            }

        ocr_response = client.ocr.process(
            model=selected_model,
            document=document_payload,
            include_image_base64=True
        )
        
        # Gom nội dung Markdown từ các trang trả về của Mistral OCR
        full_markdown = ""
        extracted_images = {}
        
        if hasattr(ocr_response, "pages"):
            for page in ocr_response.pages:
                if hasattr(page, "markdown"):
                    full_markdown += f"\n\n{page.markdown}"
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_b64_str = img.image_base64
                            if "," in img_b64_str:
                                img_b64_str = img_b64_str.split(",")[1]
                            try:
                                extracted_images[f"{img.id}.png"] = base64.b64decode(img_b64_str)
                            except:
                                pass

        # Chuyển đổi markdown sang định dạng tương thích hiển thị HTML preview
        simulated_json = {
            "pdf_info": [
                {
                    "para_blocks": [
                        {
                            "type": "text",
                            "lines": [
                                {
                                    "spans": [
                                        {"type": "text", "content": full_markdown}
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        return simulated_json, extracted_images
    except Exception as e:
        st.error(f"Lỗi khi xử lý bằng Mistral OCR: {e}")
        return None, {}


# --- 3. HÀM QUÉT ĐƯỜNG DẪN ẢNH TOÀN DIỆN ---
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


# --- 4. RENDER PREVIEW ---

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
                                    th_tag['style'] = "border: 2px solid #2d3748; padding: 6px 10px; background-color: #edf2f7; font-weight: bold; text-align: center; white-space: nowrap;"
                                for td_tag in soup.find_all("td"):
                                    td_tag['style'] = "border: 2px solid #2d3748; padding: 6px 10px; vertical-align: middle;"

                                for eq_tag in soup.find_all("eq"):
                                    eq_tag.string = clean_and_wrap_latex(eq_tag.get_text())
                                for img_tag in soup.find_all("img"):
                                    img_src = img_tag.get("src")
                                    if img_src:
                                        img_stream = get_image_bytes(img_src, images_dict, json_upload_dir)
                                        if img_stream:
                                            encoded = base64.b64encode(img_stream.getvalue()).decode("utf-8")
                                            img_tag['src'] = f"data:image/png;base64,{encoded}"
                                            img_tag['width'] = "150"
                                preview_inner_html += f"<div style='margin: 15px 0; overflow-x: auto;'>{str(soup)}</div>"

    preview_inner_html += '</div>'
    
    copier_component = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {{delimiters: [{{left: '$', right: '$', display: false}}]}});"></script>
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
            link.download = "{file_name}_MinerU.docx";
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
    st.markdown("### 👁️ Bản xem trước Toàn bộ Nội dung & Tùy chọn Lưu Word")
    components.html(copier_component, height=750, scrolling=False)


# --- 5. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2, tab3, tab4 = st.tabs([
    "🚀 Gửi lên MinerU Server (API)", 
    "🌪️ Mistral OCR Server", 
    "🌐 MinerU Web Extractor", 
    "📁 Tải file layout.json & Ảnh có sẵn (Offline)"
])

with tab1:
    st.subheader("Cấu hình API Keys & Model")
    
    col_k1, col_k2 = st.columns(2)
    with col_k1:
        api_token_input = st.text_input("Nhập MinerU API Token:", value=DEFAULT_API_KEY, type="password", disabled=not st.session_state.api_key_editable)
        if st.button("Đổi MinerU Key"):
            st.session_state.api_key_editable = not st.session_state.api_key_editable
            st.rerun()
            
    with col_k2:
        def update_gemini_key():
            st.session_state.saved_gemini_key = st.session_state.gemini_input_field

        gemini_token_input = st.text_input(
            "Nhập Gemini API Key (Dự phòng):", 
            value=st.session_state.saved_gemini_key, 
            type="password",
            key="gemini_input_field",
            on_change=update_gemini_key
        )
        if gemini_token_input:
            st.session_state.saved_gemini_key = gemini_token_input

    selected_gemini_model = st.selectbox(
        "Chọn Model Gemini dự phòng:",
        ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"],
        index=0
    )

    api_file = st.file_uploader("Chọn file PDF hoặc ảnh cần phân tích", type=["pdf", "png", "jpg", "jpeg"], key="tab1_file")
    
    if st.button("📤 Gửi & Phân tích", key="btn_tab1"):
        if not api_file:
            st.warning("Vui lòng chọn file!")
        else:
            success_processed = False
            
            with st.spinner("Đang tải file lên máy chủ trung gian..."):
                file_url = upload_temp_file_robust(api_file)
                
            if file_url:
                with st.spinner("Đang khởi tạo tác vụ xử lý trên MinerU..."):
                    task_id = start_mineru_task_by_url(api_token_input, file_url)
                if task_id:
                    st.success(f"Khởi tạo thành công! Task ID: `{task_id}`")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    for i in range(40):
                        time.sleep(3)
                        task_data = check_task_status_v4(api_token_input, task_id)
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
                            err_msg = task_data.get('err_msg') or task_data.get('error') or task_data.get('message') or "Không có mô tả chi tiết từ server"
                            st.warning(f"MinerU báo lỗi chi tiết: {err_msg}. Đang chuyển sang dự phòng bằng Gemini...")
                            break
                        progress_bar.progress(min((i + 1) * 2, 100))

            if not success_processed:
                active_key = st.session_state.saved_gemini_key.strip() or gemini_token_input.strip()
                if active_key:
                    with st.spinner(f"MinerU gặp sự cố. Đang tiến hành trích xuất bằng {selected_gemini_model}..."):
                        g_json, g_imgs = fallback_process_with_gemini(api_file, active_key, selected_gemini_model)
                        if g_json:
                            st.session_state.active_json = g_json
                            st.session_state.active_images_dict = g_imgs
                            st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                            st.success(f"Dự phòng thành công bằng {selected_gemini_model}!")
                            st.rerun()
                else:
                    st.error("MinerU thất bại và bạn chưa nhập Gemini API Key để dùng làm phương án dự phòng!")

with tab2:
    st.subheader("🌪️ Cấu hình Mistral OCR Server")
    
    def update_mistral_key():
        st.session_state.saved_mistral_key = st.session_state.mistral_input_field

    mistral_token_input = st.text_input(
        "Nhập Mistral API Key:", 
        value=st.session_state.saved_mistral_key, 
        type="password",
        key="mistral_input_field",
        on_change=update_mistral_key
    )
    if mistral_token_input:
        st.session_state.saved_mistral_key = mistral_token_input

    selected_mistral_model = st.selectbox(
        "Chọn Model Mistral OCR:",
        ["mistral-ocr-latest", "mistral-ocr-2512"],
        index=0
    )

    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh cần xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg", "avif"], key="tab2_file")
    
    if st.button("📤 Gửi & Xử lý qua Mistral", key="btn_tab2"):
        if not mistral_file:
            st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key.strip():
            st.error("Vui lòng nhập Mistral API Key!")
        else:
            with st.spinner("Đang gửi file lên Mistral OCR Server..."):
                m_json, m_imgs = process_with_mistral(mistral_file, st.session_state.saved_mistral_key.strip(), selected_mistral_model)
                if m_json:
                    st.session_state.active_json = m_json
                    st.session_state.active_images_dict = m_imgs
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Đã hoàn tất trích xuất qua Mistral OCR thành công!")
                    st.rerun()

with tab3:
    st.subheader("🌐 MinerU Web Extractor (Nhúng trực tiếp)")
    st.markdown("Trang web chính thức được nhúng bên dưới. Bạn có thể thao tác trực tiếp hoặc bấm vào nút mở tab mới nếu trình duyệt chặn khung nhúng.")
    st.markdown("[🔗 Mở trang MinerU Web Extractor trong tab mới](https://mineru.net/OpenSourceTools/Extractor)", unsafe_allow_html=True)
    
    components.iframe("https://mineru.net/OpenSourceTools/Extractor", height=650, scrolling=True)
    
    st.divider()
    st.subheader("📥 Nhận file kết quả từ Web Extractor vào Thư mục Tự động")
    st.markdown(f"Sau khi tải file giải nén từ trang web trên về máy, hãy tải file `layout.json` và thư mục `images` lên đây để hệ thống tự động lưu vào thư mục `{DEFAULT_DOWNLOAD_DIR}/` và xử lý chuyển đổi Word:")

    web_json_f = st.file_uploader("Tải file layout.json từ gói kết quả Web", type=["json"], key="web_json_tab3")
    web_image_files = st.file_uploader("Tải toàn bộ file ảnh trong thư mục images", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="web_imgs_tab3")
    
    if web_json_f:
        try:
            json_bytes = web_json_f.getvalue()
            json_path = os.path.join(DEFAULT_DOWNLOAD_DIR, web_json_f.name)
            with open(json_path, "wb") as f:
                f.write(json_bytes)

            st.session_state.active_json = json.loads(json_bytes.decode("utf-8"))
            st.session_state.active_file_name = web_json_f.name.rsplit(".", 1)[0]
            
            if web_image_files:
                images_dict = {}
                img_dir_path = os.path.join(DEFAULT_DOWNLOAD_DIR, "images")
                os.makedirs(img_dir_path, exist_ok=True)
                for img in web_image_files:
                    img_bytes = img.getvalue()
                    images_dict[img.name] = img_bytes
                    with open(os.path.join(img_dir_path, img.name), "wb") as img_f:
                        img_f.write(img_bytes)
                st.session_state.active_images_dict = images_dict
                
            st.success(f"Đã lưu và tự động nạp dữ liệu thành công từ thư mục `{DEFAULT_DOWNLOAD_DIR}/`!")
            st.rerun()
        except Exception as e:
            st.error(f"Lỗi khi xử lý file: {e}")

with tab4:
    json_f = st.file_uploader("Chọn file layout.json", type=["json"], key="offline_json")
    image_files = st.file_uploader("Chọn các file ảnh trong thư mục images", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="offline_imgs")
    if json_f:
        try:
            st.session_state.active_json = json.loads(json_f.getvalue().decode("utf-8"))
            st.session_state.active_file_name = json_f.name.rsplit(".", 1)[0]
            if image_files:
                st.session_state.active_images_dict = {img.name: img.getvalue() for img in image_files}
            st.success("Đã nạp file thành công!")
        except Exception as e:
            st.error(f"Lỗi: {e}")

if st.session_state.active_json is not None:
    st.divider()
    render_pure_math_preview(
        st.session_state.active_json, 
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )