from flask import Flask, render_template, request, send_file, redirect, session, url_for
from collections import Counter
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import os
import PyPDF2
import re
import sqlite3
from google import genai
from google.genai.errors import APIError
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mca_project_secure_fallback_key")

UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# SQLite DB Setup
def init_db():
    conn = sqlite3.connect("papers.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            email TEXT,
            password TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            summary TEXT,
            keywords TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Secure API Initialization
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

# Tenacity Retry Logic for Gemini API
@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(min=4, max=15),
    retry=retry_if_exception_type(APIError)
)
def call_gemini_safely(model_name, prompt_content):
    return client.models.generate_content(
        model=model_name,
        contents=prompt_content
    )

# ==========================================
# ROUTES & LOGIC
# ==========================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        try:
            conn = sqlite3.connect("papers.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users(username, email, password) VALUES(?,?,?)", (username, email, password))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return "Username already exists!"
            
    return render_template("register.html")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect("papers.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            session["user"] = username
            session["qa_pairs"] = []
            return redirect(url_for('home'))
        else:
            return "Invalid Username or Password"
            
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/home')
def home():
    if "user" not in session:
        return redirect(url_for('login'))
    return render_template("home.html")

@app.route('/about')
def about():
    if "user" not in session:
        return redirect(url_for('login'))
    return render_template("about.html")

@app.route('/new_chat')
def new_chat():
    if "user" not in session:
        return redirect(url_for('login'))
    
    session["qa_pairs"] = []
    latest_analysis = session.get('latest_analysis', {})

    return render_template(
        "result.html",
        filename=latest_analysis.get('filename',''),
        analysis=latest_analysis.get('analysis',''),
        keywords=latest_analysis.get('keywords',[]),
        qa_pairs=[]
    )

@app.route('/history')
def history():
    if "user" not in session:
        return redirect(url_for('login'))
    search = request.args.get('search', '')
    conn = sqlite3.connect("papers.db")
    cursor = conn.cursor()
    if search:
        cursor.execute("SELECT * FROM papers WHERE filename LIKE ?", ('%' + search + '%',))
    else:
        cursor.execute("SELECT * FROM papers")
    papers = cursor.fetchall()
    conn.close()
    return render_template("history.html", papers=papers)

@app.route('/delete/<int:id>')
def delete(id):
    if "user" not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect("papers.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM papers WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('history'))

def extract_text(pdf_path):
    text = ""
    try:
        with open(pdf_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + " "
    except Exception as e:
        print(f"Error reading PDF text: {e}")
    return text

# FIXED: Passed direct text context instead of uploading raw file to avoid 429 errors.
def generate_ai_analysis_via_text(text_content):
    prompt = f"""
Analyze the provided research paper text and return ONLY proper HTML format inside your response. Do not include markdown codeblocks (```html).
Use this format strictly:
<h2>📚 Research Domain</h2><p>...</p>
<h2>🎯 Research Objective</h2><p>...</p>
<h2>⚙️ Methodology</h2><p>...</p>
<h2>📊 Key Findings</h2><p>...</p>
<h2>⭐ Strengths</h2><p>...</p>
<h2>⚠️ Limitations</h2><p>...</p>
<h2>✅ Conclusion</h2><p>...</p>
<h2>🚀 Future Scope</h2><p>...</p>

Paper Content:
{text_content[:9000]}
"""
    try:
        response = call_gemini_safely("gemini-3.8-flash", prompt)
        return response.text
    except APIError as e:
        if e.code == 429:
            return """
            <div class='alert alert-danger'>
                <h3>⏳ Rate Limit Exceeded (API 429)</h3>
                <p>The free tier quota was temporarily exhausted. Please wait a minute and try again.</p>
            </div>
            """
        return f"Analysis Error: {str(e)}"
    except Exception as e:
        return f"Analysis Error: {str(e)}"

def extract_keywords(text):
    words = re.findall(r'[a-zA-Z]+', text.lower())
    stopwords = {'the','is','and','of','in','to','a','for','on','with','that','this','as','an','are','it','from','by','be','was'}
    filtered = [word for word in words if len(word) > 3 and word not in stopwords]
    return Counter(filtered).most_common(10)

def save_to_db(filename, summary, keywords):
    conn = sqlite3.connect("papers.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO papers(filename,summary,keywords) VALUES(?,?,?)", (filename, summary, str(keywords)))
    conn.commit()
    conn.close()

@app.route('/analyze', methods=['POST'])
def analyze():
    if "user" not in session:
        return redirect(url_for('login'))

    session["qa_pairs"] = []

    if 'pdf' not in request.files or request.files['pdf'].filename == '':
        return redirect(url_for('home'))

    pdf = request.files['pdf']
    filename = secure_filename(pdf.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    pdf.save(filepath)

    text = extract_text(filepath)
    session["paper_text"] = text

    if not text.strip():
        return render_template(
            "result.html",
            filename=filename,
            analysis="""
            <div class="alert alert-warning">
                <h3>⚠️ No Text Found</h3>
                <p>The uploaded PDF appears to be empty or contains no extractable text.</p>
            </div>
            """,
            keywords=[],
            qa_pairs=[]
        )

    # Calling the updated text-based analyzer route
    analysis = generate_ai_analysis_via_text(text)
    keywords = extract_keywords(text)

    if "Rate Limit Exceeded" not in analysis and "Analysis Error" not in analysis:
        save_to_db(filename, analysis, keywords)

    session["latest_analysis"] = {
        "filename": filename,
        "analysis": analysis,
        "keywords": keywords
    }

    return render_template(
        "result.html",
        filename=filename,
        analysis=analysis,
        keywords=keywords,
        qa_pairs=[]
    )

@app.route('/download_report')
def download_report():
    if "user" not in session:
        return redirect(url_for('login'))
        
    latest_analysis = session.get('latest_analysis', {})
    if not latest_analysis:
        return "No report data found to download.", 400

    pdf_file = "analysis_report.pdf"
    
    doc = SimpleDocTemplate(pdf_file, pagesize=letter)
    styles = getSampleStyleSheet()
    custom_body = ParagraphStyle('ReportBody', parent=styles['Normal'], fontSize=11, leading=16)
    
    story = []
    story.append(Paragraph("<b>Research Paper Analysis Report</b>", styles['Title']))
    story.append(Spacer(1, 15))
    story.append(Paragraph(f"<b>File Name:</b> {latest_analysis.get('filename','')}", styles['Heading3']))
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("<b>Summary / Analysis:</b>", styles['Heading2']))
    story.append(Spacer(1, 5))
    
    clean_text = re.sub('<[^<]+?>', '\n', latest_analysis.get('analysis', ''))
    for paragraph_text in clean_text.split('\n'):
        if paragraph_text.strip():
            story.append(Paragraph(paragraph_text.strip(), custom_body))
            story.append(Spacer(1, 8))
            
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Top Keywords:</b>", styles['Heading2']))
    
    kw_str = ", ".join([f"{k} ({v})" for k, v in latest_analysis.get('keywords', [])])
    story.append(Paragraph(kw_str, custom_body))
    
    doc.build(story)
    return send_file(pdf_file, as_attachment=True)

@app.route('/ask', methods=['POST'])
def ask_ai():
    if "user" not in session:
        return redirect(url_for('login'))
    
    qa_pairs = session.get("qa_pairs", [])
    paper_text = session.get("paper_text", "")
    latest_analysis = session.get("latest_analysis", {})
    
    question = request.form['question']
    prompt = f"Research Paper context:\n{paper_text[:6000]}\n\nQuestion: {question}\nAnswer strictly based on context:"
    
    try:
        response = call_gemini_safely('gemini-3.8-flash', prompt)
        ai_answer = response.text
    except APIError as e:
        if e.code == 429:
            ai_answer = "Rate limit hit even after multiple automatic retries. Please wait a moment before sending another query."
        else:
            ai_answer = f"Error: {str(e)}"
    except Exception as e:
        ai_answer = f"Error: {str(e)}"
    
    qa_pairs.append({"question": question, "answer": ai_answer})
    session["qa_pairs"] = qa_pairs
    
    return render_template(
        "result.html", 
        filename=latest_analysis.get('filename',''), 
        analysis=latest_analysis.get('analysis',''), 
        keywords=latest_analysis.get('keywords',[]), 
        qa_pairs=qa_pairs
    )

if __name__ == '__main__':
    app.run(debug=True)