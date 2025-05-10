from openai import OpenAI
import pandas as pd
import time
from datetime import datetime

client = OpenAI(api_key="")

df = pd.read_csv("CollectedTestsetCleaned.csv")
questions = df["Question"].dropna().tolist()
answer_model = "ft:gpt-3.5-turbo-0125:ksu::BHDqlIOS"

results = []

for idx, question in enumerate(questions):
    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=answer_model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant specialized in ADHD task support."},
                {"role": "user", "content": question}
            ],
            temperature=0.7
        )
        end_time = time.time()
        elapsed = round(end_time - start_time, 3)

        answer = response.choices[0].message.content
        results.append({
            "Question": question,
            "Response": answer,
            "Response Time (s)": elapsed
        })

        print(f"[{idx+1}/{len(questions)}] ✅ Done in {elapsed}s")

    except Exception as e:
        print(f"[{idx+1}/{len(questions)}] ❌ Error: {e}")
        results.append({
            "Question": question,
            "Response": "ERROR",
            "Response Time (s)": None
        })

df_results = pd.DataFrame(results)

avg_time = df_results["Response Time (s)"].dropna().mean()
print(f"\n📊 Average Response Time: {round(avg_time, 3)} seconds")

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"response_time_results_{timestamp}.xlsx"
df_results.to_excel(output_file, index=False)

print(f"✅ Results saved to: {output_file}")
