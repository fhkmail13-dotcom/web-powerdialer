from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def index():
    # Serve the HTML file directly
    with open('powerdialer.html', 'r') as f:
        return f.read()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
