# ContourNextLink2.4/600PumpSeries - Display and Low Alarm
Instructions to make a display of your current blood sugar level with a Low Alarm and Snooze Button (for use with a Contour Next Link 2.4 and 600 series pump)<br/><br/>
<img src="/photos/Rpi0-BGAlarm.JPG" alt="BGAlarm"
	width="300" height="200" />
## Disclaimer And Warning
* All information, thought, and code described here is intended for informational and educational purposes only.<br/>
* Make/use at your own risk, and do not use the information or code to make medical decisions.<br/>
* Use of code from github.com is without warranty or formal support of any kind. Please review this repository’s LICENSE for details.<br/>
* All product and company names, trademarks, servicemarks, registered trademarks, and registered servicemarks are the property of their respective holders. Their use is for information purposes and does not imply any affiliation with or endorsement by them.<br/>
* This project has no association with and is not endorsed by Medtronic, or any other company.<br/>

## Components/Requirements
* Raspberry Pi Zero WF (RPi0)- for wifi and headless install/no miniHDMI converter needed (https://www.adafruit.com/product/3708) 
* SD/MicroSD Card (https://www.adafruit.com/product/1294)
* TM 1637 Display (https://www.amazon.com/gp/product/B01DKISMXK/ref=ppx_yo_dt_b_asin_title_o07_s00?ie=UTF8&psc=1)
* Pushbutton (https://www.amazon.com/gp/product/B01C8CS7EI/ref=ppx_yo_dt_b_asin_title_o00_s00?ie=UTF8&psc=1)
* Piezo Buzzer (https://www.adafruit.com/product/160?gclid=EAIaIQobChMIo-L8trDn4AIVyIWzCh3VjwtYEAQYASABEgKqyPD_BwE)
* Contour Next Link 2.4 (CNL) and Medtronic 600 Series Pump
* OTG cable to connect CNL to RPi0(https://www.adafruit.com/product/1099)
* Wires to connect PB and Display to RPi0 (https://www.adafruit.com/product/1950)

## RPi0 - Configure, Install Software, Start it on bootup as Service
1. Install Raspbian Lite onto your SD Card (you don't need to format or partition it prior/the install will do it):
	* Download Raspian Lite: https://www.raspberrypi.org/downloads/raspbian/
    - Note: I used 2018-10-09-raspbian-stretch (I could not headlessly configure the wireless with the later version/ not sure why - 2018-11-13-raspbian-stretch-lite)
	* Get/install Etcher (to use to move/install Raspian onto the SD card): https://www.balena.io/etcher/
	* Open etcher and flash Raspian Lite onto your SD card (Note: this creates a small 'boot' drive and another drive - On windows i was required to format it so I created it only the default size (1.69GB/FAT)
2. On your SD card - Edit the files below (refer to: https://learn.adafruit.com/raspberry-pi-zero-creation/text-file-editing)
	* Open file: config.txt (DONT USE NOTEPAD, Use Notepad++ or an app that won't mess up formatting):
		* At bottom add:	
		```
		# Enable UART
		enable_uart=1
		```
	* Create new file called: wpa_supplicant.conf
		* Open/edit it with your info/example below (left alligned):
		```
		ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
		update_config=1
		country=US
		 
		network={
			 ssid="YOURSSID"
			 psk="YOURPASSWORD"
			 scan_ssid=1
		}
		```
	* Create a new file called ssh (no .txt extention or any extension), and don't edit it/keep it as an empty file.
3. Move the SD to the RPi0, and confirm it connects to your wireless network:
	* Eject your SD card and put it into your RPi0.  Use the 1st/TOPMOST USB PORT (if using a RPi0) and plug it into your computer - wait until green light stops blinking.
	* If you are using Windows, install Bonjour Print Services for Windows v2.0.2 - https://support.apple.com/kb/DL999?locale=en_US (I'm not sure if this is actually required but I did it)
	* Open a command prompt and try to ping the RPi0 by hostname - With windows prompt: ping raspberrypi.local
	* If you can't ping it, then you'll need to ping it by IP instead.  To figure out the RPi0's IP I have my home firewall send its event logging to: https://papertrailapp.com/.  You can setup the Rpi0 to do this as well for remote debugging, if you want.
4. SSH to the RPi0 (use PuTTY/install if you don't have):
	* Open Putty, and Enter hostname: pi@hostname(or IP) (all others default/SSH)
	* Enter default RPi0 password: raspberry
5. RPi0 Initial Setup (refer to: https://learn.adafruit.com/raspberry-pi-zero-creation/first-login)
	* Run system update:
	```
    > sudo apt-get update
    > sudo apt-get upgrade
	```
	* Run:
  	```
    > sudo raspi-config
	```
	* Select/do:
		* 'Change user password'(or similar) - change your password from default (remember it/don't loose it!)
		* 'Interfacing Options' and choose options to turn on SSH (although maybe not really needed being a boot file was made to set this on already)
		* 'Advanced Options' - Choose A1-Expand Filesystem (so that all of you SD card is available)
		* 'Update this Tool to the latest Version'

6. Install additional required Rpi0 software:
	* Confirm that you have Python 2.7 and PIP installed (Raspbian Stretch Lite comes w Python 2 and PIP for python2):
  	 ```
    > python --version
    > pip --version
	 ```
	* Install needed libraries:
	 ```
    > sudo apt-get install libusb-1.0-0-dev
    > sudo apt-get install libudev-dev
    > sudo apt-get install python-lzo
    > sudo sudo apt-get install git-core
	 ```
	* Install needed python packages:
  	 ```
    > sudo -H pip2 install cython
    > sudo -H pip2 install hidapi
    > sudo -H pip2 install requests astm PyCrypto crc16 python-dateutil
    > sudo -H pip2 install RPi.GPIO (was actually already installed)
	 ```
    - Note: If you get an error when trying any of the above installs, then read the error you get, to see which .h file or director was missing and search for a package which may include it with: apt-cache search FILENAME.  Then install it with: sudo apt-get install PACKAGENAME  
7. Git - Clone and switch to branch:
	* Clone git project:
  	 ```
    > git clone https://github.com/shelsgit/decoding-contour-next-link.git
	 ```
	* Switch to Alarm Clock Branch:
  	 ```
    > git checkout CNL-RPi0-AlarmClock
	 ```
8. Create a service to run the python clock module, at bootup:<br/>
   ** Make sure that by this step you've wired the Pushbutton and Display to the RPi0 (as in the 'Wiring' section below)  
	* Move the CNLdisplay.service file, as root to the system folder below, and then tell systemd to look for the new service:
  	 ```
    > cd /home/pi/decoding-contour-next-link/
	> sudo cp CNLdisplay.service /etc/systemd/system/CNLdisplay.service
	> sudo systemctl daemon-reload
  	 ```
	* Test it: try to start the service:
  	 ```
	> sudo systemctl start CNLdisplay.service
  	 ```
	* Check your service, to make sure its running OK:
  	 ```	
	> systemctl status CNLdisplay 
  	 ```	
	* Test it: Stop the service (and you should see 'err' on your display):
  	 ```
	> sudo systemctl stop CNLdisplay.service
	```
	* When you the service works, make it run automatically from boot up:
	```
	> sudo systemctl enable CNLdisplay.service
	```
 
## Wiring
* TM1637 Display -> RPi0 (fyi, the GPIO pin layout for a RPi0 is the same as a RPi2 or RPi3 with 40pins):
	* CLK -> GPIO23 (Pin 16, 8th pin down on right side)(top of RPi0 is when the GPIO pins are on the right)
	* DiO -> GPIO24 (Pin 18, 9th pin down on right side)
	* V -> Pi 5V Pin (Pin 2, Top pin on right side)(Could use 3V/Pin1(top,left) instead/less bright)
	* Grnd -> Pi Grnd Pin (Pin 20, 10th down on right side)
* Peizo Buzzer -> RPi0:
	* '+'-> GPIO17 (Pin 11, 6th pin down on left side)
	* Grnd -> Pi Ground Pin (Pin 9, 5th down on left side)
* Wiring Snooze Push Button -> RPi0:
	* either pushbutton terminal -> GPIO26 (left, 2nd from bottom pin)(grnd is bottom left pin)
	* other pushbutton terminal -> grnd (bottom left pin)

## Notes
* The display's 1st digit has a continual blinking underscore as a heartbeat to show that the program is running/hasn't crashed
	* IF THIS BLINKING heartbeat STOPS (for more than ~10s) this means the program crashed and data is STALE and it must be restarted<br/>
	  UPDATE: if the program crashes, the display will now say 'err' instead of only having a missing heartbeat - I kept the heartbeat during normal operation for added confidence that it's really running/updating!
	* If the blinking heartbeat stops for up to ~10s this is normal and indicates when the CNL is attempting to read from the pump
* Old/Stale Data Indication:  The display's 1st digit will show a 0 if the RPi0 missed a reading from the CNL, and an 8 if it misses another.  The display will show 8888 when it is considered 'stale', which is ~ >17min old. 
* When the pump is calibrating or a calibration is required - the display will show 'CAL'
* When the pump shows no signal - the display will show old then stale indication as described above
* If the CNL if unplugged, and plugged back in, you will have to wait up to 15 sec for display to update
* If the snooze is active and you unplug the CNL, it will buzz again when the snooze time is up until the next CNL check (when BG is not low or the data gets stale/display=8888)
* To do 1 - Make the PI readonly so it's ok to turn poweroff without properly shutting it down.  will need to turn off local logging - for now, will just use putty to restart or shutdown the pi when needed)
* To do 2 - Make a 3d case