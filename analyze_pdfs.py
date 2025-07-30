
import os
from pypdf import PdfReader

PDF_DIR = "/home/imort/smeta_2/pdf/"

def analyze_pdfs():
    total_pages = 0
    total_size_mb = 0
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
    
    print(f"Analyzing {len(pdf_files)} PDF files...")
    print("-" * 40)
    
    for filename in sorted(pdf_files):
        path = os.path.join(PDF_DIR, filename)
        try:
            with open(path, 'rb') as f:
                reader = PdfReader(f)
                pages = len(reader.pages)
                size_bytes = os.path.getsize(path)
                size_mb = size_bytes / (1024 * 1024)
                
                total_pages += pages
                total_size_mb += size_mb
                
                print(f"File: {filename:<10} | Pages: {pages:<5} | Size: {size_mb:.2f} MB")
        except Exception as e:
            print(f"File: {filename:<10} | Error: {e}")
            
    print("-" * 40)
    print(f"TOTALS:")
    print(f"Total Files: {len(pdf_files)}")
    print(f"Total Pages: {total_pages}")
    print(f"Total Size: {total_size_mb:.2f} MB")
    print("-" * 40)

if __name__ == "__main__":
    analyze_pdfs()
