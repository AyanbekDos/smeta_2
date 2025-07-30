import os
import logging
import asyncio
import time
from dotenv import load_dotenv
import google.generativeai as genai

# --- Configuration ---
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

PDF_DIR = "/home/imort/smeta_2/pdf/"
PROMPT_FILE = "find_and_validate.txt"
MAX_RETRIES = 3

# --- Pricing for gemini-2.5-flash-lite ---
# Prices per 1,000,000 tokens are $0.10 (input) and $0.40 (output)
# We convert this to price per 1,000 tokens for easier calculation.
INPUT_PRICE_PER_1000_TOKENS = 0.10 / 1000
OUTPUT_PRICE_PER_1000_TOKENS = 0.40 / 1000

def get_prompt(file_path: str) -> str:
    """Reads a prompt from a file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logging.error(f"Prompt file not found: {file_path}")
        return ""

async def find_spec_page(pdf_path: str, model, prompt: str) -> dict:
    """
    Uploads a PDF, asks for the page number, and returns results including token counts.
    """
    retries = 0
    last_exception = None
    while retries < MAX_RETRIES:
        try:
            logging.info(f"Processing file: {os.path.basename(pdf_path)} (Attempt {retries + 1})")
            gemini_file = genai.upload_file(path=pdf_path)
            
            # Get token counts
            prompt_tokens = await model.count_tokens_async([prompt, gemini_file])
            
            response = await model.generate_content_async([prompt, gemini_file])
            
            # Ensure response.usage_metadata exists and has the required fields
            if response.usage_metadata and hasattr(response.usage_metadata, 'total_token_count'):
                total_tokens = response.usage_metadata.total_token_count
                output_tokens = total_tokens - prompt_tokens.total_tokens
            else:
                # Fallback if usage_metadata is not as expected
                output_tokens = 0 # Cannot determine accurately
                total_tokens = prompt_tokens.total_tokens

            genai.delete_file(gemini_file.name)
            
            return {
                "text": response.text.strip(),
                "input_tokens": prompt_tokens.total_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens
            }

        except Exception as e:
            last_exception = e
            if "500" in str(e) or "internal error" in str(e).lower():
                retries += 1
                wait_time = 5 * (2 ** (retries - 1))
                logging.warning(f"Server error. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logging.error(f"Non-retriable error processing {os.path.basename(pdf_path)}: {e}")
                return {"text": f"Error: {e}", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    logging.error(f"Failed to process {os.path.basename(pdf_path)} after {MAX_RETRIES} attempts.")
    return {"text": f"Error: {last_exception}", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

async def main():
    """
    Main function to run the test on all PDFs, calculate costs, and summarize.
    """
    prompt = get_prompt(PROMPT_FILE)
    if not prompt:
        logging.critical(f"Could not read prompt file {PROMPT_FILE}. Exiting.")
        return

    # ИСПРАВЛЕНО: Используем gemini-2.5-flash-lite
    model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
    
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
    if not pdf_files:
        logging.warning(f"No PDF files found in {PDF_DIR}")
        return

    logging.info(f"--- Starting Test on {len(pdf_files)} PDF Files using gemini-2.5-flash-lite ---")

    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    results = {}

    for pdf_file in sorted(pdf_files):
        pdf_path = os.path.join(PDF_DIR, pdf_file)
        result_data = await find_spec_page(pdf_path, model, prompt)
        results[pdf_file] = result_data
        
        input_tokens = result_data.get("input_tokens", 0)
        output_tokens = result_data.get("output_tokens", 0)
        
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        
        cost = ((input_tokens / 1000) * INPUT_PRICE_PER_1000_TOKENS) + \
               ((output_tokens / 1000) * OUTPUT_PRICE_PER_1000_TOKENS)
        total_cost += cost
        
        print(f"File: {pdf_file}, Result: {result_data['text']}, Tokens: (In: {input_tokens}, Out: {output_tokens}), Cost: ${cost:.6f}")

    logging.info("--- Test Finished ---")
    print("\n--- Final Summary ---")
    for pdf_file, result in results.items():
        print(f"{pdf_file}: {result['text']}")
    
    print(f"\nTotal Input Tokens: {total_input_tokens}")
    print(f"Total Output Tokens: {total_output_tokens}")
    print(f"Total Estimated Cost: ${total_cost:.6f}")

if __name__ == "__main__":
    if not os.getenv("GEMINI_API_KEY"):
        logging.critical("FATAL: GEMINI_API_KEY environment variable is not set!")
    else:
        asyncio.run(main())