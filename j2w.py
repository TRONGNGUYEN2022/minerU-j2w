import os
import re
import base64
import pypandoc
import streamlit as st

try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

st.set_page_config(page_title="Convert PDF to Word with Pandoc", page_icon="📐", layout="wide")

st.title("📐 Convert PDF to Word (Mistral OCR + Pandoc)")

# Khởi tạo các giá trị trong Session State để tránh lỗi AttributeError
if "preview_markdown" not in st.session_state:
    st.session_state.preview_markdown = ""
if "images_dict" not in st.session_state:
    st.session_state.images_dict = {}
if "docx_bytes" not in st.session_state:
    st.session_state.docx_bytes = None
if "file_name" not in st.session_state:
    st.session_state.file_name = "Document"

# Cấu hình API Key
mistral_api_key = st.text_input("Nhập Mistral API Key:", type="password")

uploaded_pdf = st.file_uploader("Chọn file PDF cần chuyển đổi", type=["pdf"])

if uploaded_pdf and st.button("🚀 Gửi PDF lên Mistral OCR"):
    if not mistral_api_key:
        st.error("Vui lòng nhập Mistral API Key!")
    elif not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai` trong requirements.txt!")
    else:
        with st.spinner("Đang gửi PDF lên Mistral OCR API và tạo file Word..."):
            try:
                # 1. Gọi API Mistral OCR
                client = Mistral(api_key=mistral_api_key)
                file_bytes = uploaded_pdf.getvalue()
                base64_file = base64.b64encode(file_bytes).decode('utf-8')

                ocr_response = client.ocr.process(
                    document={
                        "type": "document_url",
                        "document_url": f"data:application/pdf;base64,{base64_file}"
                    },
                    model="mistral-ocr-latest",
                    include_image_base64=True,
                    include_blocks=True
                )
                
                # 2. Tổng hợp nội dung Markdown và lưu toàn bộ ảnh ra thư mục gốc
                full_markdown = ""
                root_dir = "."
                images_dict = {}
                
                if hasattr(ocr_response, "pages"):
                    for idx, page in enumerate(ocr_response.pages):
                        page_md = page.markdown if hasattr(page, "markdown") else ""
                        full_markdown += f"\n\n---\n### Trang {idx+1}\n\n" + page_md
                        
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
                                    except: pass

                # 3. Gọi trực tiếp Pandoc để biên dịch sẵn file Word
                temp_md_path = "temp_input.md"
                with open(temp_md_path, "w", encoding="utf-8") as f:
                    f.write(full_markdown)
                    
                output_docx = "Mistral_Output.docx"
                pypandoc.convert_file(
                    temp_md_path,
                    'docx',
                    outputfile=output_docx,
                    extra_args=['--standalone']
                )
                
                # Đọc byte file Word
                with open(output_docx, "rb") as f:
                    docx_bytes = f.read()

                # Lưu vào Session State an toàn
                st.session_state.preview_markdown = full_markdown
                st.session_state.images_dict = images_dict
                st.session_state.docx_bytes = docx_bytes
                st.session_state.file_name = uploaded_pdf.name.rsplit('.', 1)[0]
                
                if os.path.exists(temp_md_path):
                    os.remove(temp_md_path)
                    
                st.success("🎉 Xử lý thành công! Vui lòng xem bản xem trước và tải file Word bên dưới.")
                
            except Exception as e:
                st.error(f"Đã xảy ra lỗi trong quá trình xử lý: {e}")

# ==========================================
# 👁️ KHUNG PREVIEW VÀ NÚT TẢI XUỐNG
# ==========================================
if st.session_state.preview_markdown:
    st.divider()
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("👁️ Bản xem trước Nội dung & Hình ảnh")
    with col2:
        if st.session_state.docx_bytes:
            st.download_button(
                label="📥 Tải xuống file Word (.docx)",
                data=st.session_state.docx_bytes,
                file_name=f"{st.session_state.file_name}_Converted.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True
            )

    # Xử lý hiển thị base64 ảnh cho preview
    preview_md = st.session_state.preview_markdown
    
    def replace_img_base64(match):
        alt_text = match.group(1)
        img_filename = os.path.basename(match.group(2))
        if img_filename in st.session_state.images_dict:
            b64_data = base64.b64encode(st.session_state.images_dict[img_filename]).decode('utf-8')
            return f"![{alt_text}](data:image/jpeg;base64,{b64_data})"
        return match.group(0)

    preview_md = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_img_base64, preview_md)
    
    preview_md = preview_md.replace(r"\(", "$").replace(r"\)", "$")
    preview_md = preview_md.replace(r"\[", "$$").replace(r"\]", "$$")

    with st.container(border=True):
        st.markdown(preview_md, unsafe_allow_html=True)