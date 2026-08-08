import os
import re
import zipfile
import base64
import pypandoc
import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

st.set_page_config(page_title="Convert PDF to Word with Pandoc & Preview", page_icon="📐", layout="wide")

st.title("📐 Convert PDF to Word (Mistral OCR API + Preview + Pandoc)")

# Cấu hình API Key
mistral_api_key = st.text_input("Nhập Mistral API Key:", type="password")

uploaded_pdf = st.file_uploader("Chọn file PDF cần chuyển đổi", type=["pdf"])

if uploaded_pdf and st.button("🚀 Gửi PDF lên Mistral OCR & Tạo Preview"):
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
                images_dict = {}
                
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
                                        images_dict[img_filename] = img_bytes
                                        with open(os.path.join(root_dir, img_filename), "wb") as img_f:
                                            img_f.write(img_bytes)
                                    except: pass

                st.session_state.preview_markdown = full_markdown
                st.session_state.images_dict = images_dict
                st.session_state.file_name = uploaded_pdf.name.rsplit('.', 1)[0]
                st.success("🎉 Trích xuất từ Mistral thành công! Đang hiển thị bản xem trước bên dưới.")
                
            except Exception as e:
                st.error(f"Đã xảy ra lỗi trong quá trình xử lý: {e}")

# Hiển thị Khung Preview nếu đã có dữ liệu
if "preview_markdown" in st.session_state and st.session_state.preview_markdown:
    st.divider()
    st.subheader("👁️ Bản xem trước Nội dung & Hình ảnh")
    
    # Chuyển đổi markdown sang HTML hiển thị base64 ảnh cho preview trực quan
    preview_html_content = st.session_state.preview_markdown
    def replace_img_tag(match):
        img_filename = os.path.basename(match.group(2))
        if img_filename in st.session_state.images_dict:
            enc = base64.b64encode(st.session_state.images_dict[img_filename]).decode("utf-8")
            return f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{enc}" style="max-width: 450px; border-radius: 6px; border: 1px solid #cbd5e0;" /></div>'
        return match.group(0)

    preview_html_content = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_img_tag, preview_html_content)
    formatted_preview_html = preview_html_content.replace("\n", "<br>")

    preview_container = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {{delimiters: [{{left: '$', right: '$', display: false}}, {{left: '$$', right: '$$', display: true}}]}});"></script>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 15px; background-color: #ffffff; color: #2d3748; line-height: 1.8; }}
            .preview-box {{ padding: 30px; border-radius: 10px; border: 1px solid #cbd5e0; max-height: 600px; overflow-y: auto; background: #fff; }}
            img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; border-radius: 6px; }}
        </style>
    </head>
    <body>
        <div class="preview-box">
            {formatted_preview_html}
        </div>
    </body>
    </html>
    """
    components.html(preview_container, height=650, scrolling=True)

    # Nút thực hiện biên dịch và tải file Word qua Pandoc
    if st.button("💾 Biên dịch và Tải file Word (.docx)"):
        with st.spinner("Đang chạy Pandoc để biên dịch file Word..."):
            temp_md_path = "temp_input.md"
            with open(temp_md_path, "w", encoding="utf-8") as f:
                f.write(st.session_state.preview_markdown)
                
            output_docx = "Mistral_Output.docx"
            try:
                pypandoc.convert_file(
                    temp_md_path,
                    'docx',
                    outputfile=output_docx,
                    extra_args=['--standalone']
                )
                
                with open(output_docx, "rb") as file:
                    st.download_button(
                        label="📥 Click để Tải xuống file Word",
                        data=file,
                        file_name=f"{st.session_state.file_name}_Converted.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                st.success("✅ Biên dịch file Word hoàn tất!")
            except Exception as e:
                st.error(f"Lỗi Pandoc: {e}")
            finally:
                if os.path.exists(temp_md_path):
                    os.remove(temp_md_path)