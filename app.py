from flask import Flask, request, jsonify
import os
import json
from signalwire.rest import Client as signalwire_client
import threading
import time

app = Flask(__name__)

# SignalWire credentials
SIGNALWIRE_PROJECT_ID = 'f05d8022-cfd5-47f3-827c-6826baf6bea0'
SIGNALWIRE_AUTH_TOKEN = 'PT299bef850416cbec4a8c13b95f852a4bff1c26896c2330e5'
SIGNALWIRE_SPACE_URL = 'kingfinancial.signalwire.com'
FROM_NUMBER = '+12056969060'
TO_NUMBER = '+12073471301'  # Your cell number

# Initialize SignalWire client
client = signalwire_client(SIGNALWIRE_PROJECT_ID, SIGNALWIRE_AUTH_TOKEN, signalwire_space_url=SIGNALWIRE_SPACE_URL)

# Global state
contacts = []
active_calls = {}
call_count = 0
is_dialing = False
is_paused = False
simultaneous_dials = 1

@app.route('/')
def index():
    with open('powerdialer.html', 'r') as f:
        return f.read()

@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    global contacts
    try:
        file = request.files['csv']
        if not file:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        content = file.read().decode('utf-8')
        lines = content.strip().split('\n')
        
        if len(lines) < 2:
            return jsonify({'success': False, 'error': 'CSV file is empty'})
        
        # Parse header
        headers = [h.strip().lower() for h in lines[0].split(',')]
        name_col = next((i for i, h in enumerate(headers) if 'name' in h), None)
        phone_col = next((i for i, h in enumerate(headers) if 'phone' in h), None)
        
        if name_col is None or phone_col is None:
            return jsonify({'success': False, 'error': 'CSV must have "name" and "phone" columns'})
        
        # Parse contacts
        contacts = []
        for line in lines[1:]:
            row = [cell.strip() for cell in line.split(',')]
            if len(row) > max(name_col, phone_col):
                name = row[name_col]
                phone = row[phone_col]
                if name and phone:
                    contacts.append({'name': name, 'phone': phone})
        
        return jsonify({'success': True, 'count': len(contacts)})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/start_dialing', methods=['POST'])
def start_dialing():
    global is_dialing, is_paused, simultaneous_dials
    
    data = request.get_json()
    simultaneous_dials = data.get('simultaneous', 1)
    
    is_dialing = True
    is_paused = False
    
    # Start dialing in background thread
    threading.Thread(target=dial_contacts, daemon=True).start()
    
    return jsonify({'success': True})

@app.route('/pause_dialing', methods=['POST'])
def pause_dialing():
    global is_paused
    is_paused = True
    return jsonify({'success': True})

@app.route('/resume_dialing', methods=['POST'])
def resume_dialing():
    global is_paused
    is_paused = False
    return jsonify({'success': True})

@app.route('/skip_contact', methods=['POST'])
def skip_contact():
    # Skip current contacts and move to next batch
    if is_dialing and not is_paused:
        threading.Thread(target=dial_contacts, daemon=True).start()
    return jsonify({'success': True})

@app.route('/hangup_calls', methods=['POST'])
def hangup_calls():
    global is_dialing, is_paused, active_calls
    
    is_dialing = False
    is_paused = False
    
    # Hangup all active calls
    for call_id in list(active_calls.keys()):
        try:
            client.calls(call_id).update(status='completed')
        except:
            pass
    
    active_calls = {}
    return jsonify({'success': True})

@app.route('/status')
def get_status():
    global call_count, active_calls, is_dialing, is_paused, contacts
    
    status_text = 'Ready'
    if is_dialing:
        if is_paused:
            status_text = 'Paused'
        else:
            status_text = f'Dialing {len(active_calls)} calls...'
    
    # Get connection info
    connection_info = ''
    for call_data in active_calls.values():
        if call_data.get('status') == 'answered':
            contact = call_data.get('contact', {})
            connection_info = f"Connected: {contact.get('name', '')} - {contact.get('phone', '')}"
            break
    
    return jsonify({
        'call_count': call_count,
        'status': status_text,
        'connection_info': connection_info,
        'active_calls': len(active_calls),
        'contacts_remaining': len(contacts)
    })

def dial_contacts():
    global call_count, active_calls, contacts
    
    while is_dialing and not is_paused and contacts and len(active_calls) < simultaneous_dials:
        if contacts:
            contact = contacts.pop(0)
            call_count += 1
            
            try:
                # Make SignalWire call
                call = client.calls.create(
                    from_=FROM_NUMBER,
                    to=contact['phone'],
                    url=f'http://web-production-18e23.up.railway.app/call_handler',
                    status_callback=f'http://web-production-18e23.up.railway.app/call_status',
                    machine_detection='DetectMessageEnd',
                    machine_detection_timeout=10
                )
                
                active_calls[call.sid] = {
                    'contact': contact,
                    'status': 'calling',
                    'call_sid': call.sid,
                    'start_time': time.time()
                }
                
                print(f"Started call to {contact['name']} ({contact['phone']}): {call.sid}")
                
            except Exception as e:
                print(f"Error calling {contact['name']}: {e}")
        
        time.sleep(2)  # Pause between calls

@app.route('/call_handler', methods=['POST'])
def call_handler():
    """Handle answered calls - bridge to your phone"""
    call_sid = request.form.get('CallSid')
    call_status = request.form.get('CallStatus')
    
    if call_sid in active_calls:
        active_calls[call_sid]['status'] = 'answered'
    
    # TwiML to bridge the call to your phone
    twiml = f"""
    <Response>
        <Say voice="alice">Call connecting, please wait.</Say>
        <Dial timeout="30">
            <Number>{TO_NUMBER}</Number>
        </Dial>
    </Response>
    """
    
    return twiml, 200, {'Content-Type': 'application/xml'}

@app.route('/call_status', methods=['POST'])
def call_status():
    """Handle call status updates"""
    call_sid = request.form.get('CallSid')
    call_status = request.form.get('CallStatus')
    
    if call_sid in active_calls:
        if call_status in ['completed', 'failed', 'busy', 'no-answer']:
            del active_calls[call_sid]
        else:
            active_calls[call_sid]['status'] = call_status.lower()
    
    return '', 200

@app.route('/health')
def health():
    return "OK"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
