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

# Import thư viện mistralai chuẩn SDK 2.0
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
if "mistral_key_editable" not in st.session_state:
    st.session_state.mistral_key_editable = False
if "active_json" not in st.session_state:
    st.session_state.active_json = None
if "active_markdowns" not in st.session_state:
    st.session_state.active_markdowns = []
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

def extract_mistral_zip_and_get_data(zip_bytes):
    images_dict = {}
    pages_markdown = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        page_md_files = [f for f in namelist if "pages/" in f and f.endswith("markdown.md")]
        page_md_files.sort()
        
        for md_path in page_md_files:
            try:
                md_content = z.read(md_path).decode("utf-8")
                pages_markdown.append(md_content)
            except:
                pass
                
        for filename in namelist:
            if ("pages/" in filename or "images/" in filename) and (filename.endswith(".jpeg") or filename.endswith(".png") or filename.endswith(".jpg")):
                img_name = os.path.basename(filename)
                images_dict[img_name] = z.read(filename)
                
    return pages_markdown, images_dict

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

# --- 2. API MINERU & MISTRAL OCR ---

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
                    except: pass
                elif result_text.startswith("http"):
                    return result_text
        except Exception: continue
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

def fallback_process_with_gemini(uploaded_file, gemini_api_key, selected_model):
    if not GEMINI_AVAILABLE:
        st.error("Chưa cài đặt thư viện `google-genai`.")
        return None, {}
    try:
        client = genai.Client(api_key=gemini_api_key)
        file_bytes = uploaded_file.getvalue()
        mime_type = uploaded_file.type
        prompt = (
            "Bạn là một chuyên gia OCR và chuyển đổi tài liệu toán học. Hãy đọc tài liệu này thật chính xác tuyệt đối. "
            "Yêu cầu cực kỳ quan trọng đối với công thức toán học: Tất cả các biểu thức toán học, phân số, số mũ, ký hiệu BẮT BUỘC phải được đặt trong cặp dấu đô la ($...$ cho inline hoặc $$...$$ cho display). "
            "Trình bày kết quả cấu trúc thành một đoạn mã HTML sạch sẽ, trong đó các đoạn văn dùng thẻ <p>, tiêu đề dùng thẻ <h3> hoặc <b>, bảng biểu dùng thẻ <table>. Chỉ trả về mã HTML hoàn chỉnh."
        )
        response = client.models.generate_content(
            model=selected_model,
            contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt]
        )
        html_content = response.text
        html_content = re.sub(r"^```html\s*", "", html_content, flags=re.IGNORECASE)
        html_content = re.sub(r"\s*```$", "", html_content)
        simulated_json = {"pdf_info": [{"para_blocks": [{"type": "text", "lines": [{"spans": [{"type": "text", "content": html_content}]}]}]}]}
        return simulated_json, {}
    except Exception as e:
        st.error(f"Lỗi Gemini: {e}")
        return None, {}

def process_with_mistral(uploaded_file, mistral_api_key, selected_model):
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
        
        pages_markdown = []
        extracted_images = {}
        if hasattr(ocr_response, "pages"):
            for page in ocr_response.pages:
                if hasattr(page, "markdown"):
                    pages_markdown.append(page.markdown)
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_b64_str = img.image_base64
                            if "," in img_b64_str: img_b64_str = img_b64_str.split(",")[1]
                            try: extracted_images[f"{img.id}.jpeg"] = base64.b64decode(img_b64_str)
                            except: pass
        return pages_markdown, extracted_images
    except Exception as e:
        st.error(f"Lỗi Mistral OCR: {e}")
        return [], {}

# --- 3. RENDER PREVIEW (MINERU & MISTRAL) ---

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
                            if re.match(r"^Bài\s+\d+", content.strip()): p_text += f"<b>{content}</b>"
                            else: p_text += content
                        elif span_type in ["inline_equation", "equation", "math"]:
                            p_text += f" {clean_and_wrap_latex(content)} "
                if p_text.strip():
                    if "HƯỚNG DẪN CHẤM" in p_text or "Đáp án" in p_text:
                        preview_inner_html += f"<h3 style='color: #2b6cb0; margin-top: 20px;'>{p_text}</h3>"
                    else:
                        preview_inner_html += f"<p style='margin-bottom: 10px;'>{p_text}</p>"
            elif b_type in ["image", "chart", "figure"]:
                all_img_paths = []
                for key in ["image_path", "img_path", "path", "src"]:
                    val = block.get(key)
                    if val: all_img_paths.append(val)
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
            .btn-word {{ background-color: #2f855a; }}
            .preview-card {{ background-color: #ffffff; padding: 30px; border-radius: 10px; border: 1px solid #cbd5e0; max-height: 600px; overflow-y: auto; }}
        </style>
    </head>
    <body>
        <div>
            <button class="btn-action btn-copy" onclick="copyContentToClipboard()">📋 Sao chép nhanh (Dán vào Word)</button>
            <button class="btn-action btn-word" onclick="saveAsWordDocx()">💾 Lưu thành file Word (.docx)</button>
        </div>
        <div class="preview-card" id="preview-box">{preview_inner_html}</div>
        <script>
        function copyContentToClipboard() {{
            const range = document.createRange();
            range.selectNode(document.getElementById('content-to-copy'));
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            document.execCommand('copy');
            window.getSelection().removeAllRanges();
            alert('Đã sao chép vào bộ nhớ tạm!');
        }}
        function saveAsWordDocx() {{
            const contentHTML = document.getElementById('preview-box').innerHTML;
            const converted = htmlDocx.asBlob('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + contentHTML + '</body></html>');
            const link = document.createElement('a');
            link.href = URL.createObjectURL(converted);
            link.download = "{file_name}_MinerU.docx";
            link.click();
        }}
        </script>
    </body>
    </html>
    """
    st.markdown("### 👁️ Bản xem trước Nội dung & Tùy chọn Lưu Word (MinerU JSON)")
    components.html(copier_component, height=750, scrolling=False)

def render_mistral_markdown_preview(pages_markdown, images_dict, file_name="Document"):
    full_md_combined = ""
    for idx, md in enumerate(pages_markdown):
        for img_name, img_bytes in images_dict.items():
            if img_name in md:
                encoded = base64.b64encode(img_bytes).decode("utf-8")
                md = md.replace(f"({img_name})", f"(data:image/jpeg;base64,{encoded})")
        full_md_combined += f"\n\n<div style='page-break-after: always;'></div>\n\n## Trang {idx+1}\n\n" + md

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
            body {{ font-family: Arial, sans-serif; padding: 15px; background: #fff; color: #333; }}
            .btn-action {{ padding: 10px 20px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 10px; margin-bottom: 15px; }}
            .btn-copy {{ background-color: #2b6cb0; }}
            .btn-word {{ background-color: #2f855a; }}
            .preview-card {{ padding: 30px; border: 1px solid #cbd5e0; border-radius: 8px; max-height: 600px; overflow-y: auto; background: #fff; }}
            img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; }}
        </style>
    </head>
    <body>
        <div>
            <button class="btn-action btn-copy" onclick="copyContent()">📋 Sao chép nhanh</button>
            <button class="btn-action btn-word" onclick="saveWord()">💾 Tải file Word (.docx)</button>
        </div>
        <div class="preview-card" id="preview-box"></div>
        <script>
            const markdownText = {json.dumps(full_md_combined)};
            document.getElementById('preview-box').innerHTML = marked.parse(markdownText);
            renderMathInElement(document.getElementById('preview-box'));

            function copyContent() {{
                const range = document.createRange();
                range.selectNode(document.getElementById('preview-box'));
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                document.execCommand('copy');
                window.getSelection().removeAllRanges();
                alert('Đã sao chép vào bộ nhớ tạm!');
            }}

            function saveWord() {{
                const html = document.getElementById('preview-box').innerHTML;
                const converted = htmlDocx.asBlob('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + html + '</body></html>');
                const link = document.createElement('a');
                link.href = URL.createObjectURL(converted);
                link.download = "{file_name}_Mistral.docx";
                link.click();
            }}
        </script>
    </body>
    </html>
    """
    st.markdown("### 👁️ Bản xem trước Markdown từ Mistral OCR")
    components.html(preview_component, height=750, scrolling=False)

# --- 4. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2, tab3, tab4 = st.tabs([
    "🚀 Gửi lên MinerU Server (API)", 
    "🌪️ Mistral OCR Server", 
    "🌐 MinerU Web Extractor", 
    "📁 Tải file kết quả ZIP / JSON (Offline)"
])

with tab1:
    st.subheader("Cấu hình API Keys & Model")
    api_token_input = st.text_input("MinerU API Token:", value=DEFAULT_API_KEY, type="password")
    gemini_token_input = st.text_input("Gemini API Key (Dự phòng):", value=st.session_state.saved_gemini_key, type="password")
    if gemini_token_input: st.session_state.saved_gemini_key = gemini_token_input
    
    selected_gemini_model = st.selectbox("Model Gemini:", ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"], index=0)
    api_file = st.file_uploader("Chọn file PDF hoặc ảnh", type=["pdf", "png", "jpg", "jpeg"], key="t1")
    
    if st.button("📤 Gửi & Phân tích", key="b1"):
        if not api_file: st.warning("Vui lòng chọn file!")
        else:
            success_processed = False
            file_url = upload_temp_file_robust(api_file)
            if file_url:
                task_id = start_mineru_task_by_url(api_token_input, file_url)
                if task_id:
                    for _ in range(40):
                        time.sleep(3)
                        td = check_task_status_v4(api_token_input, task_id)
                        state = td.get("state")
                        if state == "done":
                            r = requests.get(td.get("full_zip_url"))
                            if r.status_code == 200:
                                f_json, imgs = extract_zip_and_get_data(r.content)
                                st.session_state.active_json = f_json
                                st.session_state.active_markdowns = []
                                st.session_state.active_images_dict = imgs
                                st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                                success_processed = True
                                st.success("Hoàn tất bằng MinerU!")
                                st.rerun()
                            break
                        elif state == "failed": break
            
            if not success_processed:
                active_key = st.session_state.saved_gemini_key.strip() or gemini_token_input.strip()
                if active_key:
                    g_json, g_imgs = fallback_process_with_gemini(api_file, active_key, selected_gemini_model)
                    if g_json:
                        st.session_state.active_json = g_json
                        st.session_state.active_markdowns = []
                        st.session_state.active_images_dict = g_imgs
                        st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                        st.success(f"Dự phòng thành công bằng {selected_gemini_model}!")
                        st.rerun()

with tab2:
    st.subheader("🌪️ Mistral OCR Server")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    selected_mistral_model = st.selectbox("Chọn Model Mistral OCR:", ["mistral-ocr-latest"], index=0)
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg", "avif"], key="t2")
    
    if st.button("📤 Gửi & Xử lý qua Mistral", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang xử lý qua Mistral OCR..."):
                m_mds, m_imgs = process_with_mistral(mistral_file, st.session_state.saved_mistral_key, selected_mistral_model)
                if m_mds:
                    st.session_state.active_markdowns = m_mds
                    st.session_state.active_json = None
                    st.session_state.active_images_dict = m_imgs
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Thành công qua Mistral OCR!")
                    st.rerun()

with tab3:
    st.subheader("🌐 MinerU Web Extractor")
    components.iframe("https://mineru.net/OpenSourceTools/Extractor", height=650, scrolling=True)

with tab4:
    st.subheader("📁 Tải file kết quả ZIP (Mistral) hoặc layout.json (MinerU) Offline")
    uploaded_zip_or_json = st.file_uploader("Chọn file ZIP hoặc layout.json", type=["zip", "json"], key="offline_upload")
    image_files = st.file_uploader("Chọn các file ảnh phụ (nếu có)", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="offline_imgs")
    
    if uploaded_zip_or_json:
        if uploaded_zip_or_json.name.endswith(".zip"):
            m_mds, m_imgs = extract_mistral_zip_and_get_data(uploaded_zip_or_json.getvalue())
            if m_mds:
                st.session_state.active_markdowns = m_mds
                st.session_state.active_json = None
                st.session_state.active_images_dict = m_imgs
                st.session_state.active_file_name = uploaded_zip_or_json.name.rsplit(".", 1)[0]
                st.success("Đã nạp file ZIP Mistral thành công!")
                st.rerun()
        else:
            try:
                st.session_state.active_json = json.loads(uploaded_zip_or_json.getvalue().decode("utf-8"))
                st.session_state.active_markdowns = []
                st.session_state.active_file_name = uploaded_zip_or_json.name.rsplit(".", 1)[0]
                if image_files:
                    st.session_state.active_images_dict = {img.name: img.getvalue() for img in image_files}
                st.success("Đã nạp layout.json thành công!")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi: {e}")

# Điều phối khung xem trước tương ứng
if st.session_state.active_markdowns:
    st.divider()
    render_mistral_markdown_preview(
        st.session_state.active_markdowns,
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )
elif st.session_state.active_json is not None:
    st.divider()
    render_pure_math_preview(
        st.session_state.active_json,
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )