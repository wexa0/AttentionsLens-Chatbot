import pandas as pd
import numpy as np
from sklearn.metrics import recall_score, confusion_matrix
from evaluate import load
from openai import OpenAI
from evaluate import load


client = OpenAI(api_key="")
model_id = "ft:gpt-3.5-turbo-0125:ksu::BHDqlIOS"

df = pd.read_csv("CollectedTestsetCleaned.csv")
df.columns = ['Question', 'Answer']


print("\n point 1")


def get_gpt_answer(question):
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[  {
    "role": "system",
    "content": "You are an ADHD productivity assistant. Provide a clear, concise, and accurate answer to the following ADHD-related question only. Avoid long unrelated information."
  },
  {
    "role": "user",
    "content": question
  }],
            max_tokens=900,
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error: {e}")
        return ""

df['GPT_Answer'] = df['Question'].apply(get_gpt_answer)


print("\n point 2")


print("\n point 3")

# Load metrics
bleu = load("bleu")
rouge = load("rouge")
bertscore = load("bertscore")

# BLEU
bleu_result = bleu.compute(predictions=df['GPT_Answer'].tolist(),
                           references=[[x] for x in df['Answer'].tolist()])['bleu'] 


# ROUGE
rouge_result = rouge.compute(predictions=df['GPT_Answer'].tolist(),
                             references=df['Answer'].tolist())

# BERTScore
bertscore_result = bertscore.compute(predictions=df['GPT_Answer'].tolist(),
                                     references=df['Answer'].tolist(),
                                     lang="en")


# Print Results
print("===== Evaluation Results =====")
print(f"BLEU Score: {bleu_result * 100:.2f}%")
print(f"ROUGE-1: {rouge_result['rouge1'] * 100:.2f}%")
print(f"ROUGE-2: {rouge_result['rouge2'] * 100:.2f}%")
print(f"ROUGE-L: {rouge_result['rougeL'] * 100:.2f}%")
print(f"BERTScore (F1): {sum(bertscore_result['f1']) / len(bertscore_result['f1']) * 100:.2f}%")

