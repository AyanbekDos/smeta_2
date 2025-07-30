#!/usr/bin/env python3
"""
🔥 НОВЫЙ ЭТАП 2b: OCR коррекция через Gemini 2.5 Pro

ЦЕЛЬ: Точное исправление ошибок OCR перед извлечением данных
- Исправление размеров профилей (20Б1 → 20Ш1, [16n → 16п)
- Коррекция марок стали (С5 → Ст3, С6 → С235)  
- Нормализация названий профилей
- Использует мощный Gemini 2.5 Pro для максимальной точности
"""

import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional
import google.generativeai as genai

# Добавляем корень проекта в путь
sys.path.append(str(Path(__file__).parent.parent))

from config.config import GEMINI_API_KEY, TEMP_DIR, ensure_directories
from utils.logger import setup_logger, log_step
from utils.file_utils import ensure_file_exists
from utils.token_counter import create_token_counter

logger = setup_logger(__name__)

class OCRCorrector:
    """Класс для коррекции ошибок OCR через Gemini 2.5 Pro"""
    
    def __init__(self, project_path: str = None):
        """
        Инициализация корректора OCR
        
        Args:
            project_path: Путь к проекту для метрик токенов
        """
        self.setup_gemini()
        self.token_counter = create_token_counter(project_path)
        
        logger.info("🎯 Инициализирован OCR корректор на Gemini 2.5 Pro")
    
    def setup_gemini(self):
        """Настройка Gemini 2.5 Pro"""
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY не найден!")
        
        genai.configure(
            api_key=GEMINI_API_KEY,
            transport='rest'
        )
        
        # Используем самую мощную модель для точной коррекции
        self.model = genai.GenerativeModel('gemini-2.5-pro')
        logger.info("🧠 Gemini 2.5 Pro настроен для OCR коррекции")
    
    def create_ocr_correction_prompt(self, ocr_text: str) -> str:
        """
        Создает промпт для коррекции OCR ошибок
        
        Используем контекстную коррекцию для металлопроката
        """
        
        prompt = f"""
Ты специалист по спецификациям металлоконструкций. Нужно откорректировать таблицу, сохраняя исходное расположение ячеек. Размеры профилей могут содержать ошибки OCR — исправляй их, используя интеллектуальный контекст и указанные правила.

ДАННЫЕ И ПРАВИЛА КОРРЕКЦИИ:
1. Профиль — тип металлопроката (например, "Прокат листовой горячекатаный ГОСТ такой-то").
2. Размер — обязательное поле, содержит габариты или обозначения профиля (например, "s5", "125 Б 2", "I20 W1"). Исправь распространенные OCR ошибки в размерах, учитывая:
 - "[16n" → "16п" (швеллер 16 с параллельными полками)
 - "[200" → "200" (швеллер 200)
 - "[120" → "120" (швеллер 120)
 - "[22n" → "22п" (швеллер 22 с параллельными полками)
 - "C247" → "24У" (швеллер 24 с уклоном полок)
 - "+5" → "s5" (лист толщиной 5 мм)
 - "nucm" → "лист" (просечно-вытяжной лист)
 - Символы "[" и "]" часто ошибочно распознаются вместо букв или цифр и требуют исправления.
3. Марка стали — обязательна, извлекай из колонки "Наименование или марка металла ГОСТ, ТУ"; исправляй OCR ошибки:
 - "Ст5" → "Ст3"
 - "С245РТ" → "С245"
 - "С255КТ" → "С255"
 - Поддерживаем стандартные марки: Ст3, С245, С255, С285, С345, С375, С390, С440, 09Г2С, 10ХСНД, 15ХСНД.
4. Масса — числовое значение, берется строго из соответствующей колонки назначения.
5. Элемент металлоконструкций определяется по названию колонки с массой: "балки", "стойки", "настил", "связи", "лестницы", "ограждения" и др.
6. Если у одного профиля разные веса в разных колонках назначения — считаем это разными записями.
7. Для нескольких записей с одинаковым профилем сгенерируй уникальное значение поля "поз" (например, "11-1", "11-2", "11-3").
8. Пропускай итоговые строки вроде: "Итого", "Всего профиля".
9. Числа записывай с точкой (например, 1.25, не 1,25).
10. Если данные нечитаемы или очень неразборчивы — используй значение null.

📋 ВОЗВРАТ:
Верни только исправленную таблицу в исходной структуре без лишних комментариев или пояснений.

🔍 Начинай корректировку.
"""
        
        return prompt
    
    def correct_ocr_text(self, ocr_text: str) -> Dict[str, Any]:
        """
        Исправляет ошибки OCR через Gemini 2.5 Pro
        
        Args:
            ocr_text: Исходный текст после OCR
            
        Returns:
            Словарь с исправленным текстом и метриками
        """
        
        logger.info("🔧 Начинаю коррекцию OCR через Gemini 2.5 Pro...")
        
        try:
            # Создаем промпт
            prompt = self.create_ocr_correction_prompt(ocr_text)
            
            # Отправляем запрос
            logger.info("📤 Отправляю запрос на коррекцию...")
            
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,  # Минимальная креативность - нужна точность
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=8192,
                )
            )
            
            if not response or not response.text:
                raise Exception("Пустой ответ от Gemini 2.5 Pro")
            
            corrected_text = response.text.strip()
            
            # Подсчитываем токены и стоимость
            input_tokens = response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else len(prompt) // 4
            output_tokens = response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else len(corrected_text) // 4
            
            metrics = self.token_counter.count_tokens_and_cost(
                model="gemini-2.5-pro",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                operation="OCR коррекция"
            )
            
            # Логируем метрики
            self.token_counter.log_metrics(logger, metrics)
            
            # Анализ изменений
            changes_made = self._analyze_changes(ocr_text, corrected_text)
            
            logger.info(f"✅ OCR коррекция завершена: {changes_made['changes_count']} изменений")
            
            return {
                "original_text": ocr_text,
                "corrected_text": corrected_text,
                "changes_analysis": changes_made,
                "token_metrics": metrics,
                "success": True,
                "error": None
            }
            
        except Exception as e:
            error_msg = f"Ошибка OCR коррекции: {e}"
            logger.error(error_msg)
            
            return {
                "original_text": ocr_text,
                "corrected_text": ocr_text,  # Fallback на исходный текст
                "changes_analysis": {"changes_count": 0, "types": []},
                "token_metrics": None,
                "success": False,
                "error": error_msg
            }
    
    def _analyze_changes(self, original: str, corrected: str) -> Dict[str, Any]:
        """Анализирует изменения сделанные в тексте"""
        
        if original == corrected:
            return {"changes_count": 0, "types": [], "examples": []}
        
        # Простой анализ различий
        original_lines = original.split('\n')
        corrected_lines = corrected.split('\n')
        
        changes = []
        changes_count = 0
        
        for i, (orig_line, corr_line) in enumerate(zip(original_lines, corrected_lines)):
            if orig_line != corr_line:
                # Находим различия в строке
                orig_words = orig_line.split()
                corr_words = corr_line.split()
                
                for j, (orig_word, corr_word) in enumerate(zip(orig_words, corr_words)):
                    if orig_word != corr_word:
                        changes.append({
                            "line": i + 1,
                            "position": j + 1,
                            "original": orig_word,
                            "corrected": corr_word,
                            "type": self._classify_change_type(orig_word, corr_word)
                        })
                        changes_count += 1
        
        # Группируем типы изменений
        change_types = list(set(change["type"] for change in changes))
        
        return {
            "changes_count": changes_count,
            "types": change_types,
            "examples": changes[:10],  # Первые 10 примеров
            "size_change": len(corrected) - len(original)
        }
    
    def _classify_change_type(self, original: str, corrected: str) -> str:
        """Классифицирует тип изменения"""
        
        # Простая классификация
        if any(char.isdigit() for char in original) and any(char.isdigit() for char in corrected):
            return "размер_профиля"
        elif original.upper().startswith(('С', 'C', 'СТ')):
            return "марка_стали"
        elif len(original) > 10 and len(corrected) > 10:
            return "название_профиля"
        else:
            return "прочее"

@log_step("OCR коррекция через Gemini 2.5 Pro", logger)
def correct_ocr_errors(input_path: str, output_path: str = None) -> str:
    """
    Основная функция коррекции OCR ошибок
    
    Args:
        input_path: Путь к HTML файлу после OCR
        output_path: Путь к исправленному файлу
        
    Returns:
        Путь к исправленному файлу
    """
    
    input_path = Path(input_path)
    
    if not ensure_file_exists(input_path):
        raise FileNotFoundError(f"Входной файл не найден: {input_path}")
    
    # Определяем путь для сохранения (в той же папке что и входной файл)
    if output_path is None:
        output_path = input_path.parent / f"{input_path.stem}_corrected.html"
    else:
        output_path = Path(output_path)
    
    logger.info(f"🔧 OCR коррекция: {input_path.name} → {output_path.name}")
    
    # Читаем исходный HTML
    with open(input_path, 'r', encoding='utf-8') as f:
        original_html = f.read()
    
    # Создаем корректор
    corrector = OCRCorrector(str(input_path.parent))
    
    # Исправляем ошибки
    result = corrector.correct_ocr_text(original_html)
    
    if result["success"]:
        # Сохраняем исправленный текст
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(result["corrected_text"])
        
        # Логируем результаты
        changes = result["changes_analysis"]
        logger.info(f"✅ Коррекция завершена:")
        logger.info(f"   📝 Изменений: {changes['changes_count']}")
        logger.info(f"   🔧 Типы: {', '.join(changes['types']) if changes['types'] else 'нет'}")
        
        # Показываем примеры изменений
        if changes['examples']:
            logger.info("📋 Примеры исправлений:")
            for example in changes['examples'][:3]:
                logger.info(f"   • {example['original']} → {example['corrected']} ({example['type']})")
        
        # Итоговые метрики сессии
        corrector.token_counter.log_session_summary(logger)
        
    else:
        logger.warning("⚠️ Коррекция не удалась, используется исходный файл")
        # Копируем исходный файл
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(original_html)
    
    return str(output_path)

def main():
    """Основная функция для запуска из командной строки"""
    
    if len(sys.argv) < 2:
        print("🔧 OCR КОРРЕКЦИЯ. Использование:")
        print("python 2b_ocr_correction.py <input_html> [output_html]")
        print("Пример: python 2b_ocr_correction.py temp/page_1_ocr.html")
        print("💡 Исправляет ошибки OCR через Gemini 2.5 Pro")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        result_path = correct_ocr_errors(input_path, output_path)
        print(f"SUCCESS: {result_path}")
        print("✅ OCR коррекция завершена успешно!")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()