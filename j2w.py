import os
import re
import zipfile
import base64
import pypandoc
import requests
import streamlit as st

try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

st.set_page_config(page_title="Convert PDF to Word with Pandoc", page_icon="📐", layout="wide")

st.title("📐 Convert PDF to Word (Mistral OCR API + Pandoc)")

# Cấu hình API Key
mistral_api_key = st.text_input("Nhập Mistral API Key:", type="password")

uploaded_pdf = st.file_uploader("Chọn file PDF cần chuyển đổi", type=["pdf"])

if uploaded_pdf and st.button("🚀 Gửi PDF lên Mistral OCR & Xuất Word"):
    if not mistral_api_key:
        st.error("Vui lòng nhập Mistral API Key!")
    elif not MISTRAL_AVAILABLE:
        st.error("Chưa cài đặt thư viện `mistralai` trong requirements.txt!")
    else:
        with st.spinner("Đang gửi PDF lên Mistral OCR API để trích xuất văn bản và hình ảnh..."):
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
                                        # Lưu ảnh ra thư mục gốc để Pandoc nhận diện theo tên trong markdown
                                        with open(os.path.join(root_dir, img_filename), "wb") as img_f:
                                            img_f.write(img_bytes)
                                    except: pass

                # 3. Ghi nội dung markdown ra file tạm để Pandoc biên dịch
                temp_md_path = "temp_input.md"
                with open(temp_md_path, "w", encoding="utf-8") as f:
                    f.write(full_markdown)
                    
                # 4. Biên dịch sang file Word (.docx) bằng Pandoc
                output_docx = "Mistral_Output.docx"
                pypandoc.convert_file(
                    temp_md_path,
                    'docx',
                    outputfile=output_docx,
                    extra_args=['--standalone']
                )
                
                st.success("🎉 Trích xuất từ Mistral và biên dịch file Word thành công!")
                
                # Cung cấp nút tải file Word
                with open(output_docx, "rb") as file:
                    st.download_button(
                        label="📥 Tải xuống file Word (.docx)",
                        data=file,
                        file_name=f"{uploaded_pdf.name.rsplit('.', 1)[0]}_Converted.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                    
                if os.path.exists(temp_md_path):
                    os.remove(temp_md_path)
                    
            except Exception as e:
                st.error(f"Đã xảy ra lỗi trong quá trình xử lý: {e}")