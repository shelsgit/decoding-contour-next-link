# ContourNextLink2.4/600PumpSeries - Display and Low Alarm
Instructions to make a Display of current blood sugar level with a Low Alarm and Snooze Button (for use with a Contour Next Link 2.4 and 600 series pump)<br/>

## Disclaimer And Warning
* All information, thought, and code described here is intended for informational and educational purposes only.<br/>
* Make/use at your own risk, and do not use the information or code to make medical decisions.<br/>
* Use of code from github.com is without warranty or formal support of any kind. Please review this repository’s LICENSE for details.<br/>
* All product and company names, trademarks, servicemarks, registered trademarks, and registered servicemarks are the property of their respective holders. Their use is for information purposes and does not imply any affiliation with or endorsement by them.<br/>
* This project has no association with and is not endorsed by Medtronic, or any other company.<br/>

## Components/Wiring/Requirements
* Raspberry Pi Zero WF (RPi0)- for wifi and headless install/no miniHDMI converter needed (https://www.adafruit.com/product/3708) 
* Micro SD Card
* TM 1637 Display (https://www.amazon.com/gp/product/B01DKISMXK/ref=ppx_yo_dt_b_asin_title_o07_s00?ie=UTF8&psc=1)
* Pushbutton (https://www.amazon.com/gp/product/B01C8CS7EI/ref=ppx_yo_dt_b_asin_title_o00_s00?ie=UTF8&psc=1)
* Contour Next Link 2.4 (CNL) and Medtronic 600 Series Pump
* OTG cable - to connect CNL to RPi0
* Wires to solder PB and Display to RPi0

## Configure RPi0
1. Install Raspbian Lite onto your SD Card (you don't need to format or partition it prior/the install will do it):
  * Download Raspian Lite: https://www.raspberrypi.org/downloads/raspbian/
    - Note: I used 2018-10-09-raspbian-stretch (I could not headlessly configure the wireless with the later version/ not sure why - 2018-11-13-raspbian-stretch-lite)
  * Get/install Etcher (to use to move/install Raspian onto the SD card): https://www.balena.io/etcher/
  * Open etcher and flash Raspian Lite onto your SD card (Note: this creates a small 'boot' drive and another drive - On windows i was required to format it so I created it only the default size (1.69GB/FAT)
2. On your SD card - Edit the files below (refer to: https://learn.adafruit.com/raspberry-pi-zero-creation/text-file-editing)
  * Open file: config.txt (DONT USE NOTEPAD, Use Notepad++ or an app that won't mess up formatting):
    - At bottom add:     
    > # Enable UART
    > enable_uart=1
  * Create new file called: wpa_supplicant.conf
    - Open/edit it with your info/example below (left alligned):
    > ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
    > update_config=1
    > country=US
    >  
    > network={
    >     ssid="YOURSSID"
    >     psk="YOURPASSWORD"
    >     scan_ssid=1
    > } 
  * Create a new file called ssh (no .txt extention or any extension), and don't edit it/keep it as an empty file.
3. Move the SD to the RPi0, and confirm it connects to your wireless network:
  * Eject your SD card and put it into your RPi0.  Use the 1st/TOPMOST USB PORT (if using a RPi0) and plug it into your computer - wait until green light stops blinking.
  * If you are using Windows, install Bonjour Print Services for Windows v2.0.2 - https://support.apple.com/kb/DL999?locale=en_US (I'm not sure if this is actually required but I did it)
  * Open a command prompt and try to ping the RPi0 by hostname - With windows prompt: ping raspberrypi.local
  * If you can't ping it, then you'll need to ping it by IP instead.  To figure out the RPi0's IP - I used: https://papertrailapp.com/ (IDed it's IP: saw 'raspberry' in a recent log with its IP)
4. SSH to the RPi0 (use PuTTY/install if you don't have):
  * Open Putty, and Enter hostname: pi@hostname(or IP) (all others default/SSH)
  * Enter default RPi0 password: raspberry
5. RPi0 Initial Setup (refer to: https://learn.adafruit.com/raspberry-pi-zero-creation/first-login)
  * Run system update:
    > sudo apt-get update
    > sudo apt-get upgrade
  * Run:
    > sudo raspi-config
       - Select: Change user password and change from default (remember it/don't loose it!)
       - Select: 'Interfacing Options' and choose options to turn on SSH (although maybe not really needed being a boot file was made to set this on already)
       - Select: 'Advanced Options' - Choose A1-Expand Filesystem (so that all of you SD card is available)
       - Select: 'Update this Tool to the latest Version'
6. Install additional required Rpi0 software:
  * Confirm that you have Python 2.7 and PIP installed (Raspbian Stretch Lite comes w Python 2 and PIP for python2):  
    > python --version
    > pip --version
  * Install needed libraries:
    > sudo apt-get install libusb-1.0-0-dev
    > sudo apt-get install libudev-dev
    > sudo apt-get install python-lzo
    > sudo sudo apt-get install git-core
  * Install needed python packages:
    > sudo -H pip2 install cython
    > sudo -H pip2 install hidapi
    > sudo -H pip2 install requests astm PyCrypto crc16 python-dateutil
    > sudo -H pip2 install RPi.GPIO (was actually already installed)
    - Note: If you get an error when trying any of the above installs, then read the error you get, to see which .h file or director was missing and search for a package which may include it with: apt-cache search FILENAME.  Then install it with: sudo apt-get install PACKAGENAME  
7. Git - Clone and switch to branch:
  * Clone git project:
    > git clone https://github.com/shelsgit/decoding-contour-next-link.git
  * Switch to Alarm Clock Branch:
    > git checkout CNL-RPi0-AlarmClock
8. Run the program (Make sure you did the git step above to make sure you're in the right branch!):
> cd /home/pi/
> sudo python2.7 -m decoding-contour-next-link.read_minimed_next24
(TO CHANGE THIS SO IT RUNs on startup)
	
## Wiring
* TO FINISH

## Notes
* TO FINISH
