import datetime
import os
import io
import socketserver
import time
from threading import Condition, Thread, Event, Lock
from http import server
from picamera import PiCamera
from gpiozero import MotionSensor
import RPi.GPIO as GPIO
from time import sleep
import BlynkLib
import firebase_admin
from firebase_admin import credentials, storage, db
from flask import Flask
from flask_sslify import SSLify
import subprocess
import requests


# Initialize Firebase
cred = credentials.Certificate('./serviceAccountKey.json')
firebase_admin.initialize_app(cred, {
    'storageBucket': 'smart-pet-monitor-3efe8.appspot.com',
    'databaseURL': 'https://smart-pet-monitor-3efe8-default-rtdb.europe-west1.firebasedatabase.app/'
})
bucket = storage.bucket()
ref = db.reference('/')
home_ref = ref.child('file')

# Initialize hardware
GPIO.setmode(GPIO.BCM)  # Set pin numbering mode
xPir = MotionSensor(27)
servo_pin = 18
GPIO.setup(servo_pin, GPIO.OUT)
servo_pwm = GPIO.PWM(servo_pin, 50)  # 50 Hz frequency

# Initialize Blynk
BLYNK_TEMPLATE_ID = 'TMPL4XAFfn0x6'
BLYNK_TEMPLATE_NAME = 'Quickstart Template'
BLYNK_AUTH_TOKEN = '!!AUTH TOKEN!!'
blynk = BlynkLib.Blynk(BLYNK_AUTH_TOKEN)

# Initialize Flask app
app = Flask(__name__)
sslify = SSLify(app)

# servo position function
def start_flask():
    print("flask thread")
    try:
        servo_pwm.start(2.5)  # Start at 0 degrees position (2.5% duty cycle)
        app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)
    finally:
        servo_pwm.stop()
        GPIO.cleanup()

# servo position function
def set_servo_angle(angle):
    duty_cycle = (angle / 18) + 2.5
    servo_pwm.ChangeDutyCycle(duty_cycle)
    time.sleep(0.5)  # Allow time for the servo to reach the desired position

# Define Flask routes for servo control
@app.route('/open')
def open_door():
    set_servo_angle(0)  # Move to leftmost position
    return 'Door Opened!'

@app.route('/close')
def close_door():
    set_servo_angle(170)  # Move to rightmost position
    return 'Door Closed!'

@app.route('/stop')
def stop_servo():
    servo_pwm.stop()  # Stop the servo
    return 'Motor Stopped!'

# Function to take and store an image
def take_and_store_image():
    # Capture image
    currentTime = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    picloc = f'/home/pi/project/pictures/image_{timestamp}.jpg'
    with PiCamera(resolution='640x480', framerate=24) as pic_camera:
        pic_camera.capture(picloc)

    # Upload image to Firebase storage
    blob = bucket.blob(os.path.basename(picloc))
    blob.upload_from_filename(picloc)

    # Push file reference to Realtime Database
    home_ref.push({
        'image': os.path.basename(picloc),
        'timestamp': currentTime
	})

def streamCamera():
    duration = 60  # Duration in seconds
    start_time = time.time()  # Record the start time

    class StreamingOutput(object):
        def __init__(self):
            self.frame = None
            self.buffer = io.BytesIO()
            self.condition = Condition()

        def write(self, buf):
            if buf.startswith(b'\xff\xd8'):
                self.buffer.truncate()
                with self.condition:
                    self.frame = self.buffer.getvalue()
                    self.condition.notify_all()
                self.buffer.seek(0)
            return self.buffer.write(buf)

    class StreamingHandler(server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal start_time  # Access the start_time variable from the outer scope
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            i = 0
            try:
                while time.time() - start_time <= duration:  # Check elapsed time
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                    i += 1
            except BrokenPipeError:
                pass

    class StreamingServer(server.HTTPServer):
        allow_reuse_address = True

    with PiCamera(resolution='640x480', framerate=24) as camera:
        output = StreamingOutput()
        camera.start_recording(output, format='mjpeg')
        address = ('', 8000)
        streaming_server = StreamingServer(address, StreamingHandler)

        # Start Ngrok tunnel
        ngrok_process = subprocess.Popen(['ngrok', 'http', '--region=us', '--hostname=woofwatch.ngrok.app', '8000'])

        try:
            streaming_server.handle_request()  # Serve a single request
        except KeyboardInterrupt:
            pass
        finally:
            camera.stop_recording()
            streaming_server.server_close()

        # Terminate Ngrok process after streaming ends
        ngrok_process.terminate()

    return  # Return after the specified duration

# Main function
if __name__ == "__main__":
    # Start Flask server in a separate thread
    flask_thread = Thread(target=start_flask)
    flask_thread.start()

    # Main loop for motion detection and image capture
    while True:
        print("Main loop")
        if xPir.value == 1:
            print("Movement detected - capturing image")
            take_and_store_image()
            print("Image captured and stored")
            sleep(1)
            print("Streaming live")
            streamCamera()
        sleep(2)
