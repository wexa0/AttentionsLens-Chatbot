
import json

def count_unique_questions(file_path):

    with open(file_path, "r", encoding="utf-8") as f:
        data = f.readlines()


    unique_questions = set()
    inside_sft_block = False
    question_text = ""

    for line in data:
        line = line.strip()
        if line == "<SFT>":
            inside_sft_block = True
            question_text = ""
        elif line == "</SFT>":
            inside_sft_block = False
            if question_text:
                unique_questions.add(question_text)
        elif inside_sft_block and line.startswith("Question: "):
            question_text = line.replace("Question: ", "").strip()
    
    return len(unique_questions)

train_data_path = "train_data.json"
num_unique_samples = count_unique_questions(train_data_path)

print(f"✅number of json objects : {num_unique_samples}")
