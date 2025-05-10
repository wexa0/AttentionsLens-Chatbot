
![Welcome (19)](https://github.com/user-attachments/assets/caed207e-75da-441d-92a8-dbca934b083a)

 # AttentionLens Chatbot

This repository contains the **AI-powered assistant backend** for the AttentionLens application — a productivity and task management app specifically designed to support individuals with ADHD. The chatbot, named **Attena**, is powered by a fine-tuned GPT-3.5 Turbo model and integrates tightly with Firebase to deliver personalized, intelligent, and structured task support.

---

## 🎯 Project Purpose

The AttentionLens chatbot helps ADHD users overcome daily productivity challenges by offering:
- 📌 Clear task management assistance (add, edit, delete, view tasks)
- 💡 ADHD-focused guidance (time management, focus strategies, etc.)
- 😊 Emotionally supportive interaction with natural language processing
- 🔐 Secure data handling via Firebase integration


---

## 🚀 How to Run

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/AttentionLens.git
cd AttentionLens/chatbot_backend
```
2. Install Dependencies
Make sure you are using Python 3.9+, then run:

```bash
pip install -r requirements.txt
```

3. Add Firebase Admin Credentials
Place your Firebase service account key JSON file in the same directory. Make sure it matches the name in the code (e.g., attensionlens-db-firebase-adminsdk-fsdxz-8d6dc28e0f.json).

4. Set OpenAI API Key
Inside chatbot_finetuned_gpt3_5.py, replace the placeholder in this line with your own OpenAI key:


