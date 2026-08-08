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
if "active_mistral_json_pages" not in st.session_state:
    st.session_state.active_mistral_json_pages = []
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = ""

# --- HÀM ĐỌC ZIP MISTRAL VÀ MAP ẢNH CHUẨN XÁC ---
def extract_mistral_json_from_zip(zip_bytes):
    images_dict = {}
    pages_json_data = []
    
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        page_dirs = set()
        for f in namelist:
            match = re.search(r"pages/(page-\d+)/", f)
            if match:
                page_dirs.add(match.group(1))
        
        sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
        
        for p_dir in sorted_pages:
            page_info = {"page_name": p_dir, "blocks": [], "images": {}}
            meta_path = f"pages/{p_dir}/page-metadata.json"
            
            if meta_path in namelist:
                try:
                    meta_content = json.loads(z.read(meta_path).decode("utf-8"))
                    page_info["blocks"] = meta_content.get("blocks", [])
                except: pass
                
            # Trích xuất toàn bộ ảnh trong thư mục trang này
            for f in namelist:
                if f.startswith(f"pages/{p_dir}/") and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                    img_name = os.path.basename(f)
                    img_bytes = z.read(f)
                    page_info["images"][img_name] = img_bytes
                    images_dict[img_name] = img_bytes
                    
            pages_json_data.append(page_info)
            
    return pages_json_data, images_dict

# --- GỌI API MISTRAL OCR ---
def process_with_mistral_json(uploaded_file, mistral_api_key, selected_model):
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
        
        pages_json_data = []
        images_dict = {}
        
        if hasattr(ocr_response, "pages"):
            for idx, page in enumerate(ocr_response.pages):
                p_dir = f"page-{idx+1}"
                page_info = {"page_name": p_dir, "blocks": [], "images": {}}
                
                if hasattr(page, "markdown") and page.markdown:
                    page_info["blocks"].append({"type": "text", "content": page.markdown})
                    
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                            img_id = img.id
                            img_b64 = img.image_base64
                            if "," in img_b64: img_b64 = img_b64.split(",")[1]
                            try:
                                img_bytes = base64.b64decode(img_b64)
                                img_filename = f"{img_id}.jpeg"
                                page_info["images"][img_filename] = img_bytes
                                images_dict[img_filename] = img_bytes
                                page_info["blocks"].append({
                                    "type": "image",
                                    "imageId": img_filename,
                                    "content": f"![{img_filename}]({img_filename})"
                                })
                            except: pass
                            
                pages_json_data.append(page_info)
        return pages_json_data, images_dict
    except Exception as e:
        st.error(f"Lỗi Mistral OCR: {e}")
        return [], {}

# --- RENDER PREVIEW CÓ CHÈN ẢNH BASE64 CHÍNH XÁC ---
def render_mistral_json_preview(pages_json_data, images_dict, file_name="Document"):
    combined_html = '<div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">'
    
    for page in pages_json_data:
        p_name = page["page_name"]
        blocks = page["blocks"]
        page_images = page["images"]
        all_imgs = {**images_dict, **page_images}
        
        combined_html += f"<div style='border-bottom: 2px dashed #cbd5e0; margin-bottom: 25px; padding-bottom: 15px;'>"
        combined_html += f"<h4 style='color: #4a5568;'>📄 Trang {p_name.split('-')[1]}</h4>"
        
        for block in blocks:
            b_type = block.get("type")
            content = block.get("content", "")
            
            if b_type == "image":
                img_id = block.get("imageId")
                if not img_id:
                    m = re.search(r'\((.*?)\)', content)
                    if m: img_id = os.path.basename(m.group(1))
                
                if img_id and img_id in all_imgs:
                    encoded = base64.b64encode(all_imgs[img_id]).decode("utf-8")
                    combined_html += f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{encoded}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>'
            elif b_type in ["header", "footer"]:
                combined_html += f"<div style='font-size: 13px; color: #a0aec0; margin: 5px 0;'>{content}</div>"
            else:
                # Kiểm tra nếu trong text có chứa cú pháp ảnh markdown ![...](img_x.jpeg) thì thay thế bằng ảnh base64
                for img_name, img_bytes in all_imgs.items():
                    if img_name in content:
                        encoded = base64.b64encode(img_bytes).decode("utf-8")
                        content = content.replace(f"![{img_name}]({img_name})", f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{encoded}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>')
                
                formatted_content = content.replace("\n", "<br>")
                combined_html += f"<div>{formatted_content}</div>"
                
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
            img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; border-radius: 6px; }}
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
                link.download = "{file_name}_Mistral_FullImages.docx";
                link.click();
            }}
        </script>
    </body>
    </html>
    """
    st.markdown("### 👁️ Bản xem trước Đầy đủ Văn bản, Công thức & Hình ảnh")
    components.html(preview_component, height=780, scrolling=False)


# --- 5. GIAO DIỆN CHÍNH ---

st.title("📐 Convert PDF/Image to word")

tab1, tab2 = st.tabs([
    "🌪️ Mistral OCR (Full Images Parser)", 
    "📁 Tải file ZIP kết quả Offline"
])

with tab1:
    st.subheader("🌪️ Cấu hình Mistral OCR")
    mistral_token_input = st.text_input("Mistral API Key:", value=st.session_state.saved_mistral_key, type="password")
    if mistral_token_input: st.session_state.saved_mistral_key = mistral_token_input
    
    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="t2")
    
    if st.button("📤 Gửi & Phân tích Đầy đủ Ảnh", key="b2"):
        if not mistral_file: st.warning("Vui lòng chọn file!")
        elif not st.session_state.saved_mistral_key: st.error("Nhập Mistral API Key!")
        else:
            with st.spinner("Đang xử lý phân tích và trích xuất ảnh từng trang..."):
                pages_json, imgs = process_with_mistral_json(mistral_file, st.session_state.saved_mistral_key, "mistral-ocr-latest")
                if pages_json:
                    st.session_state.active_mistral_json_pages = pages_json
                    st.session_state.active_images_dict = imgs
                    st.session_state.active_file_name = mistral_file.name.rsplit(".", 1)[0]
                    st.success("Hoàn tất xử lý đầy đủ hình ảnh thành công!")
                    st.rerun()

with tab2:
    st.subheader("📁 Tải file ZIP kết quả Mistral Offline")
    uploaded_zip = st.file_uploader("Chọn file ZIP kết quả Mistral", type=["zip"], key="offline_zip")
    
    if uploaded_zip:
        pages_json, imgs = extract_mistral_json_from_zip(uploaded_zip.getvalue())
        if pages_json:
            st.session_state.active_mistral_json_pages = pages_json
            st.session_state.active_images_dict = imgs
            st.session_state.active_file_name = uploaded_zip.name.rsplit(".", 1)[0]
            st.success("Đã nạp và giải mã thành công file ZIP Mistral!")
            st.rerun()

# Điều phối khung xem trước
if st.session_state.active_mistral_json_pages:
    st.divider()
    render_mistral_json_preview(
        st.session_state.active_mistral_json_pages,
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )