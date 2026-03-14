from flask import Flask, request, jsonify
import os
import json
from signalwire.rest import Client as signalwire_client
import threading
import time

app = Flask(__name__)

SIGNALWIRE_PROJECT_ID = 'f05d8022-cfd5-47f3-827c-6826baf6bea0'
SIGNALWIRE_AUTH_TOKEN = 'PT299bef850416cbec4a8c13b95f852a4bff1c26896c2330e5'
SIGNALWIRE_SPACE_URL = 'kingfinancial.signalwire.com'
FROM_NUMBER = '+12056969060'
TO_NUMBER = '+12073471301'

client = signalwire_client(SIGNALWIRE_PROJECT_ID, SIGNALWIRE_AUTH_TOKEN, signalwire_space_url=SIGNALWIRE_SPACE_URL)

# Global state
contacts = []
active_calls = {}
call_count = 0
is_dialing = False
is_paused = False
simultaneous_dials = 1
connected_call = None
dialing_lock = threading.Lock()

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
        
        headers = [h.strip().lower() for h in lines[0].split(',')]
        name_col = next((i for i, h in enumerate(headers) if 'name' in h), None)
        phone_col = next((i for i, h in enumerate(headers) if 'phone' in h), None)
        email_col = next((i for i, h in enumerate(headers) if 'email' in h), None)
        
        if name_col is None or phone_col is None:
            return jsonify({'success': False, 'error': 'CSV must have "name" and "phone" columns'})
        
        contacts = []
        for line in lines[1:]:
            row = [cell.strip() for cell in line.split(',')]
            if len(row) > max(name_col, phone_col):
                name = row[name_col]
                phone = row[phone_col]
                email = row[email_col] if email_col is not None and len(row) > email_col else ''
                
                if name and phone:
                    contacts.append({'name': name, 'phone': phone, 'email': email})
        
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
    
    threading.Thread(target=maintain_dialing_slots, daemon=True).start()
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
    global active_calls
    
    if not active_calls:
        return jsonify({'success': True})
    
    longest_call_sid = None
    longest_duration = 0
    
    for call_sid, call_data in active_calls.items():
        if call_data['status'] == 'calling':
            duration = time.time() - call_data['start_time']
            if duration > longest_duration:
                longest_duration = duration
                longest_call_sid = call_sid
    
    if longest_call_sid:
        try:
            client.calls(longest_call_sid).update(status='completed')
            print(f"Skipped longest-running call: {longest_call_sid}")
        except Exception as e:
            print(f"Error skipping call: {e}")
    
    return jsonify({'success': True})

@app.route('/hangup_calls', methods=['POST'])
def hangup_calls():
    global is_dialing, is_paused, active_calls, connected_call
    
    is_dialing = False
    is_paused = False
    connected_call = None
    
    for call_id in list(active_calls.keys()):
        try:
            client.calls(call_id).update(status='completed')
        except:
            pass
    
    active_calls = {}
    return jsonify({'success': True})

@app.route('/status')
def get_status():
    global call_count, active_calls, is_dialing, is_paused, contacts, connected_call
    
    status_text = 'Ready'
    if is_dialing:
        if is_paused:
            status_text = 'Paused'
        elif connected_call:
            status_text = 'Call Connected'
        else:
            status_text = f'Dialing {len(active_calls)} calls...'
    
    connection_info = ''
    if connected_call:
        contact = connected_call.get('contact', {})
        connection_info = f"Connected: {contact.get('name', '')} - {contact.get('phone', '')}"
    
    return jsonify({
        'call_count': call_count,
        'status': status_text,
        'connection_info': connection_info,
        'active_calls': len(active_calls),
        'contacts_remaining': len(contacts)
    })

def maintain_dialing_slots():
    global active_calls, contacts, call_count, is_dialing, is_paused, connected_call
    
    while is_dialing:
        if not is_paused and not connected_call and contacts:
            
            with dialing_lock:
                active_calling_count = len([call for call in active_calls.values() 
                                          if call['status'] in ['calling', 'ringing']])
                
                slots_needed = simultaneous_dials - active_calling_count
                
                for _ in range(slots_needed):
                    if contacts:
                        contact = contacts.pop(0)
                        start_single_call(contact)
                    else:
                        break
        
        time.sleep(0.5)

def start_single_call(contact):
    global call_count, active_calls
    
    try:
        call = client.calls.create(
            from_=FROM_NUMBER,
            to=contact['phone'],
            url=f'https://web-production-18e23.up.railway.app/call_handler',
            status_callback=f'https://web-production-18e23.up.railway.app/call_status',
            machine_detection='DetectMessageEnd',
            machine_detection_timeout=10,
            timeout=20
        )
        
        call_count += 1
        active_calls[call.sid] = {
            'contact': contact,
            'status': 'calling',
            'call_sid': call.sid,
            'start_time': time.time()
        }
        
        print(f"Started call #{call_count} to {contact['name']} ({contact['phone']}): {call.sid}")
        
    except Exception as e:
        print(f"Error calling {contact['name']}: {e}")

def hangup_other_calls(except_call_sid):
    global active_calls
    
    for call_sid, call_data in list(active_calls.items()):
        if call_sid != except_call_sid:
            try:
                client.calls(call_sid).update(status='completed')
                print(f"Hung up call {call_sid}")
            except Exception as e:
                print(f"Error hanging up {call_sid}: {e}")

@app.route('/call_handler', methods=['POST'])
def call_handler():
    global connected_call, active_calls
    
    call_sid = request.form.get('CallSid')
    machine_detection = request.form.get('AnsweredBy', '')
    
    print(f"Call handler: {call_sid}, Machine: {machine_detection}")
    
    if machine_detection in ['machine_end_beep', 'machine_end_silence', 'machine_start']:
        print(f"Voicemail detected on {call_sid} - hanging up")
        with dialing_lock:
            active_calls.pop(call_sid, None)
        return '<Response><Hangup/></Response>', 200, {'Content-Type': 'application/xml'}
    
    if call_sid in active_calls:
        connected_call = active_calls[call_sid]
        connected_call['status'] = 'answered'
        
        hangup_other_calls(call_sid)
        
        print(f"Human answered on {call_sid} - bridging to your phone")
    
    twiml = f"""
    <Response>
        <Say voice="alice">Connecting your call, please wait.</Say>
        <Dial timeout="120" callerId="{FROM_NUMBER}">
            <Number>{TO_NUMBER}</Number>
        </Dial>
    </Response>
    """
    
    return twiml, 200, {'Content-Type': 'application/xml'}

@app.route('/call_status', methods=['POST'])
def call_status():
    global connected_call, active_calls
    
    call_sid = request.form.get('CallSid')
    call_status = request.form.get('CallStatus')
    
    print(f"Status update: {call_sid} = {call_status}")
    
    if call_status in ['completed', 'failed', 'busy', 'no-answer']:
        with dialing_lock:
            if call_sid in active_calls:
                del active_calls[call_sid]
                print(f"Call {call_sid} ended - slot available for refill")
        
        if connected_call and connected_call.get('call_sid') == call_sid:
            print("Connected call ended - ready to resume dialing")
            connected_call = None
    
    return '', 200

@app.route('/active_calls_data')
def active_calls_data():
    global active_calls
    calls = []
    for sid, data in active_calls.items():
        calls.append({
            'call_sid': sid,
            'name': data['contact'].get('name', ''),
            'phone': data['contact'].get('phone', ''),
            'email': data['contact'].get('email', ''),
            'status': data['status']
        })
    return jsonify({'calls': calls})

@app.route('/manual_call', methods=['POST'])
def manual_call():
    data = request.get_json()
    number = data.get('number')
    if not number:
        return jsonify({'success': False, 'error': 'No number provided'})
    contact = {'name': f'Manual: {number}', 'phone': number, 'email': ''}
    start_single_call(contact)
    return jsonify({'success': True})

@app.route('/health')
def health():
    return "OK"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
