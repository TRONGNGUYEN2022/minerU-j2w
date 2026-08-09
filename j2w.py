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

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title="Convert PDF/Image to word", page_icon="📐", layout="wide")
MINERU_BASE_URL = "https://mineru.net"
DEFAULT_API_KEY = "sk-IDb81Oj2W6pHrODooHN0xtKTxEXNzipsnZP6OxAqAl65Kz9O"

# --- KHỞI TẠO SESSION STATE ---
if "api_key_editable" not in st.session_state:
    st.session_state.api_key_editable = False
if "active_json" not in st.session_state:
    st.session_state.active_json = None
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "extracted_images_dir" not in st.session_state:
    st.session_state.extracted_images_dir = ""
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "MinerU_Document"

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
    """Hàm upload file tạm thông minh với cơ chế dự phòng nhiều server"""
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
            
    st.error("Tất cả các server upload tạm đều đang bận hoặc lỗi. Vui lòng thử lại sau ít phút!")
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
            return response.json().get("data", {})
    except: pass
    return None


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


# --- 4. RENDER PREVIEW (BẢNG BIỂU TỰ ĐỘNG FIT THEO NỘI DUNG & ĐƯỜNG VIỀN ĐẬM) ---

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

tab1, tab2 = st.tabs(["🚀 Gửi lên MinerU Server (API)", "📁 Tải file layout.json & Ảnh có sẵn (Offline)"])

with tab1:
    st.subheader("Cấu hình API Key")
    col_k1, col_k2 = st.columns([5, 1])
    with col_k1:
        api_token_input = st.text_input("Nhập API Token:", value=DEFAULT_API_KEY, type="password", disabled=not st.session_state.api_key_editable)
    with col_k2:
        st.write("")
        st.write("")
        if st.button("Change / Đổi"):
            st.session_state.api_key_editable = not st.session_state.api_key_editable
            st.rerun()

    api_file = st.file_uploader("Chọn file PDF hoặc ảnh cần phân tích", type=["pdf", "png", "jpg", "jpeg"])
    
    if st.button("📤 Gửi & Phân tích"):
        if not api_file:
            st.warning("Vui lòng chọn file!")
        else:
            with st.spinner("Đang tải file lên máy chủ trung gian (có dự phòng tự động)..."):
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
                        status_text.text(f"Trạng thái: {state}")
                        if state == "done":
                            full_zip_url = task_data.get("full_zip_url")
                            r_zip = requests.get(full_zip_url)
                            if r_zip.status_code == 200:
                                found_json, images_dict = extract_zip_and_get_data(r_zip.content)
                                if found_json:
                                    st.session_state.active_json = found_json
                                    st.session_state.active_images_dict = images_dict
                                    st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                                    st.success("Đã hoàn tất phân tích!")
                                    st.rerun()
                            break
                        elif state == "failed":
                            st.error(f"Xử lý thất bại: {task_data.get('err_msg')}")
                            break
                        progress_bar.progress(min((i + 1) * 2, 100))

with tab2:
    json_f = st.file_uploader("Chọn file layout.json", type=["json"])
    image_files = st.file_uploader("Chọn các file ảnh trong thư mục images", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
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