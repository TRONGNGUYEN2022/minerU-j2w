import os
import re
import zipfile
import pypandoc
import streamlit as st

st.set_page_config(page_title="Convert PDF to Word with Pandoc", page_icon="📐", layout="wide")

st.title("📐 Convert PDF to Word (Mistral OCR + Pandoc)")

uploaded_zip = st.file_uploader("Tải lên file ZIP kết quả từ Mistral OCR", type=["zip"])

if uploaded_zip:
    if st.button("🚀 Xử lý và Xuất file Word bằng Pandoc"):
        with st.spinner("Đang giải nén, gom ảnh ra thư mục gốc và biên dịch bằng Pandoc..."):
            
            # 1. Giải nén và gom toàn bộ ảnh ra thư mục gốc
            root_dir = "."
            markdown_content = ""
            
            with zipfile.ZipFile(uploaded_zip) as z:
                namelist = z.namelist()
                
                # Đọc nội dung file markdown gốc nếu có, hoặc tổng hợp từ các trang
                if "markdown.md" in namelist:
                    markdown_content = z.read("markdown.md").decode("utf-8")
                else:
                    page_mds = []
                    page_dirs = set()
                    for f in namelist:
                        match = re.search(r"pages/(page-\d+)/", f)
                        if match: page_dirs.add(match.group(1))
                    sorted_pages = sorted(list(page_dirs), key=lambda x: int(x.split("-")[1]))
                    
                    for p_dir in sorted_pages:
                        md_path = f"pages/{p_dir}/markdown.md"
                        if md_path in namelist:
                            page_mds.append(z.read(md_path).decode("utf-8"))
                    markdown_content = "\n\n".join(page_mds)
                
                # Quét tất cả các file ảnh trong ZIP và copy ra thư mục gốc
                for f in namelist:
                    if ("pages/" in f or "images/" in f or "/" not in f) and (f.endswith(".jpeg") or f.endswith(".png") or f.endswith(".jpg")):
                        img_name = os.path.basename(f)
                        if img_name:
                            img_bytes = z.read(f)
                            # Lưu trực tiếp tại thư mục gốc để Pandoc dễ dàng tìm thấy theo tên
                            with open(os.path.join(root_dir, img_name), "wb") as img_f:
                                img_f.write(img_bytes)
                                
            # 2. Ghi nội dung markdown ra file tạm để Pandoc đọc
            temp_md_path = "temp_input.md"
            with open(temp_md_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)
                
            # 3. Gọi Pandoc chuyển đổi Markdown sang Docx
            output_docx = "Mistral_Output.docx"
            try:
                pypandoc.convert_file(
                    temp_md_path,
                    'docx',
                    outputfile=output_docx,
                    extra_args=['--standalone']
                )
                st.success("🎉 Biên dịch thành công file Word bằng Pandoc!")
                
                # Cung cấp nút tải file Word xuống
                with open(output_docx, "rb") as file:
                    st.download_button(
                        label="📥 Tải xuống file Word (.docx)",
                        data=file,
                        file_name="Converted_Math_Document.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
            except Exception as e:
                st.error(f"Lỗi khi chạy Pandoc: {e}")
            finally:
                if os.path.exists(temp_md_path):
                    os.remove(temp_md_path)