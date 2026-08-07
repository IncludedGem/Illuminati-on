import serial.tools.list_ports
 
print("Available serial ports:")
print("=" * 50)
 
ports = serial.tools.list_ports.comports()
 
if not ports:
    print("No serial ports found!")
else:
    for port in ports:
        print(f"Port: {port.device}")
        print(f"Description: {port.description}")
        print(f"Hardware ID: {port.hwid}")
        print("-" * 50)
 
