#!/usr/bin/env python

import logging
# logging.basicConfig has to be before astm import, otherwise logs don't appear
# Logging - filemode=w overrights logfile each time script is ran, .DEBUG, shows all info,warning and debug logs, .WARNING shows warning + higher, .ERROR error+)
logging.basicConfig(filename='read_mini.log',filemode='w',format='%(asctime)s %(levelname)s [%(name)s] %(message)s',level=logging.ERROR)
# a workaround on missing hidapi.dll on my windows (allows testing from saved files, but not download of pump)
try:
    import hid # pip install hidapi - Platform independant
except WindowsError:
    pass
import astm # pip install astm
import struct
import binascii
import datetime
import time
from time import sleep
import crc16 # pip install crc16
import Crypto.Cipher.AES # pip install PyCrypto
import sqlite3
import hashlib
import re
import pickle # needed for local history export
import lzo # pip install python-lzo
# from .pump_history_parser import NGPHistoryEvent, BloodGlucoseReadingEvent
from .helpers import DateTimeHelper
import tm1637 #from github-timwaizenegger/raspberrypi-examples, and copied to working dir
import RPi.GPIO as IO #also imported in tm1637, maybe I can delete from there? (did, testing)

###############################################################
# Wiring: TM1637 Display -> Raspberry Raspberry Pi Zero (RPi0) (fyi, GPIO for RPi0 is same for other RPis(2,3) that have 40pins):
# CLK -> GPIO23 (Pin 16, 8th pin down on right side of RPi0)
# DiO -> GPIO24 (Pin 18, 9th pin down on right side of RPi0)
# V -> Pi 5V Pin (Pin 2, Top pin on right side or RPi0)(Could use 3V/Pin1(top,left) instead/less bright)
# Grnd -> Pi Grnd Pin (Pin 20, 10th down on right side of RPi0)

# Wiring: Buzzer -> Rpi3:
# + -> GPIO17 (Pin 11, 6th pin down on left side of Rpi3)
# Grnd -> Pi Ground Pin (Pin 9, 5th down on right side of Rpi3)

# Wiring Snooze Push Button -> Rpi3:
# A(topleft of button) -> GPIO26 (left, 2nd from bottom pin)(grnd is bottom left pin)
# C(bottomleft of button) -> grnd (bottom left pin or Rpi3)
###############################################################

#SETPOINTS 
BGlowSP = 70 # low alarm/buzzer setpoint (mg/dL)
SnoozeTimeSP = 20 # time to snooze after snooze PB pressed (minutes)

#Define globals
realpumptime = 0
BGLtime = 0
timediff = 0
noCNL = 0
BGL = 777

logger = logging.getLogger(__name__)

ascii= {
    'ACK' : 0x06,
    'CR' : 0x0D,
    'ENQ' : 0x05,
    'EOT' : 0x04,
    'ETB' : 0x17,
    'ETX' : 0x03,
    'LF' : 0x0A,
    'NAK' : 0x15,
    'STX' : 0x02
}

def ord_hack(char_or_byte):
    return char_or_byte if isinstance(char_or_byte, int) else ord(char_or_byte)

class COM_D_COMMAND:
    HIGH_SPEED_MODE_COMMAND = 0x0412
    TIME_REQUEST = 0x0403
    TIME_RESPONSE = 0x0407
    READ_PUMP_STATUS_REQUEST = 0x0112
    READ_PUMP_STATUS_RESPONSE = 0x013C
    READ_BASAL_PATTERN_REQUEST = 0x0116
    READ_BASAL_PATTERN_RESPONSE = 0x0123
    READ_BOLUS_WIZARD_CARB_RATIOS_REQUEST = 0x012B
    READ_BOLUS_WIZARD_CARB_RATIOS_RESPONSE = 0x012C
    READ_BOLUS_WIZARD_SENSITIVITY_FACTORS_REQUEST = 0x012E
    READ_BOLUS_WIZARD_SENSITIVITY_FACTORS_RESPONSE = 0x012F
    READ_BOLUS_WIZARD_BG_TARGETS_REQUEST = 0x0131
    READ_BOLUS_WIZARD_BG_TARGETS_RESPONSE = 0x0132
    DEVICE_STRING_REQUEST = 0x013A
    DEVICE_STRING_RESPONSE = 0x013B
    DEVICE_CHARACTERISTICS_REQUEST = 0x0200
    DEVICE_CHARACTERISTICS_RESPONSE = 0x0201
    READ_HISTORY_REQUEST = 0x0304
    READ_HISTORY_RESPONSE = 0x0305
    END_HISTORY_TRANSMISSION = 0x030A
    READ_HISTORY_INFO_REQUEST = 0x030C
    READ_HISTORY_INFO_RESPONSE = 0x030D
    UNMERGED_HISTORY_RESPONSE = 0x030E
    INITIATE_MULTIPACKET_TRANSFER = 0xFF00
    MULTIPACKET_SEGMENT_TRANSMISSION = 0xFF01
    MULTIPACKET_RESEND_PACKETS = 0xFF02
    ACK_MULTIPACKET_COMMAND = 0x00FE # TODO ACK_COMMAND
    NAK_COMMAND = 0x00FF
    BOLUSES_REQUEST = 0x0114
    REMOTE_BOLUS_REQUEST = 0x0100
    REQUEST_0x0124 = 0x0124
    REQUEST_0x0405 = 0x0405
    TEMP_BASAL_REQUEST = 0x0115
    SUSPEND_RESUME_REQUEST = 0x0107
    NGP_PARAMETER_REQUEST = 0x0138

class HISTORY_DATA_TYPE:
    PUMP_DATA = 0x02
    SENSOR_DATA = 0x03

class TimeoutException( Exception ):
    pass

class ChecksumException( Exception ):
    pass

class UnexpectedMessageException( Exception ):
    pass

class UnexpectedStateException( Exception ):
    pass

class NegotiationException( Exception ):
    pass

class InvalidMessageError( Exception ):
    pass

class ChecksumError( Exception ):
    pass

class DataIncompleteError( Exception ):
    pass

class Config( object ):
    def __init__( self, stickSerial ):
        self.conn = sqlite3.connect( 'read_minimed.db' )
        self.c = self.conn.cursor()
        self.c.execute( '''CREATE TABLE IF NOT EXISTS
            config ( stick_serial TEXT PRIMARY KEY, hmac TEXT, key TEXT, last_radio_channel INTEGER )''' )
        self.c.execute( "INSERT OR IGNORE INTO config VALUES ( ?, ?, ?, ? )", ( stickSerial, '', '', 0x14 ) )
        self.conn.commit()

        self.loadConfig( stickSerial )

    def loadConfig( self, stickSerial ):
        self.c.execute( 'SELECT * FROM config WHERE stick_serial = ?', ( stickSerial, ) )
        self.data = self.c.fetchone()

    @property
    def stickSerial( self ):
        return self.data[0]

    @property
    def lastRadioChannel( self ):
        return self.data[3]

    @lastRadioChannel.setter
    def lastRadioChannel( self, value ):
        self.c.execute( "UPDATE config SET last_radio_channel = ? WHERE stick_serial = ?", ( value, self.stickSerial ) )
        self.conn.commit()
        self.loadConfig( self.stickSerial )

    @property
    def hmac( self ):
        return self.data[1]

    @hmac.setter
    def hmac( self, value ):
        self.c.execute( "UPDATE config SET hmac = ? WHERE stick_serial = ?", ( value, self.stickSerial ) )
        self.conn.commit()
        self.loadConfig( self.stickSerial )

    @property
    def key( self ):
        return self.data[2]

    @key.setter
    def key( self, value ):
        self.c.execute( "UPDATE config SET key = ? WHERE stick_serial = ?", ( value, self.stickSerial ) )
        self.conn.commit()
        self.loadConfig( self.stickSerial )

class MedtronicSession( object ):
    radioChannel = None
    bayerSequenceNumber = 1
    minimedSequenceNumber = 1
    sendSequenceNumber = 0

    @property
    def HMAC( self ):
        serial = bytearray( re.sub( r"\d+-", "", self.stickSerial ), 'ascii' ) 
        paddingKey = b"A4BD6CED9A42602564F413123"
        digest = hashlib.sha256(serial + paddingKey).hexdigest()
        return "".join(reversed([digest[i:i+2] for i in range(0, len(digest), 2)]))

    @property
    def hexKey( self ):
        if self.config.key == "":
            raise Exception( "Key not found in config database. Run get_hmac_and_key.py to get populate HMAC and key." )
        return self.config.key

    @property
    def stickSerial( self ):
        return self._stickSerial

    @stickSerial.setter
    def stickSerial( self, value ):
        self._stickSerial = value
        self.config = Config( self.stickSerial )
        self.radioChannel = self.config.lastRadioChannel

    @property
    def linkMAC( self ):
        return self._linkMAC

    @linkMAC.setter
    def linkMAC( self, value ):
        self._linkMAC = value

    @property
    def pumpMAC( self ):
        return self._pumpMAC

    @pumpMAC.setter
    def pumpMAC( self, value ):
        self._pumpMAC = value

    @property
    def linkSerial( self ):
        return self.linkMAC & 0xffffff

    @property
    def pumpSerial( self ):
        return self.pumpMAC & 0xffffff

    @property
    def KEY( self ):
        return self._key

    @KEY.setter
    def KEY( self, value ):
        self._key = value

    @property
    def IV( self ):
        tmp = bytearray()
        tmp.append(self.radioChannel)
        tmp += self.KEY[1:]        
        return bytes(tmp)

class MedtronicMessage( object ):
    ENVELOPE_SIZE = 2

    def __init__( self, commandAction=None, session=None, payload=None ):
        self.commandAction = commandAction
        self.session = session
        if payload:
            self.setPayload( payload )

    def setPayload( self, payload ):
        self.payload = payload
        self.envelope = struct.pack( '<BB', self.commandAction,
            len( self.payload ) + self.ENVELOPE_SIZE )

    @classmethod
    def calculateCcitt( self, data ):
        crc = crc16.crc16xmodem( bytes(data), 0xffff )
        return crc & 0xffff

    def pad( self, x, n = 16 ):
        p = n - ( len( x ) % n )
        return x + bytes(bytearray(p))#chr(p) * p

    # Encrpytion equivalent to Java's AES/CFB/NoPadding mode
    def encrypt( self, clear ):
        cipher = Crypto.Cipher.AES.new(
            key=self.session.KEY,
            mode=Crypto.Cipher.AES.MODE_CFB,
            IV=self.session.IV,
            segment_size=128
        )

        encrypted = cipher.encrypt(self.pad(clear))[0:len(clear)]
        return encrypted

    # Decryption equivalent to Java's AES/CFB/NoPadding mode
    def decrypt( self, encrypted ):
        cipher = Crypto.Cipher.AES.new(
            key=self.session.KEY,
            mode=Crypto.Cipher.AES.MODE_CFB,
            IV=self.session.IV,
            segment_size=128
        )

        decrypted = cipher.decrypt(self.pad(encrypted))[0:len(encrypted)]
        return decrypted

    def encode( self ):
        # Increment the Minimed Sequence Number
        self.session.minimedSequenceNumber += 1
        message = self.envelope + self.payload
        crc = struct.pack( '<H', crc16.crc16xmodem( message, 0xffff ) & 0xffff )
        return message + crc

    @classmethod
    def decode( cls, message, session ):
        response = cls()
        response.session = session
        response.envelope = message[0:2]
        response.payload = message[2:-2]
        response.originalMessage = message;

        checksum = struct.unpack( '<H', message[-2:] )[0]
        calcChecksum = MedtronicMessage.calculateCcitt( response.envelope + response.payload )
        if( checksum != calcChecksum ):
            logger.info("ChecksumException.")
            raise ChecksumException( 'Expected to get {0}. Got {1}'.format( calcChecksum, checksum ) )

        return response

class ChannelNegotiateMessage( MedtronicMessage ):
    def __init__( self, session ):
        MedtronicMessage.__init__( self, 0x03, session )

        # The minimedSequenceNumber is always sent as 1 for this message,
        # even though the sequence should keep incrementing as normal
        payload = struct.pack( '<BB8s', 1, session.radioChannel,
            b'\x00\x00\x00\x07\x07\x00\x00\x02' )
        payload += struct.pack( '<Q', session.linkMAC )
        payload += struct.pack( '<Q', session.pumpMAC )

        self.setPayload( payload )

class MedtronicSendMessage( MedtronicMessage ):
    def __init__( self, messageType, session, payload=None ):
        MedtronicMessage.__init__( self, 0x05, session )

        # FIXME - make this not be hard coded
        if messageType == COM_D_COMMAND.HIGH_SPEED_MODE_COMMAND:
            seqNo = self.session.sendSequenceNumber | 0x80
        else:
            seqNo = self.session.sendSequenceNumber

        encryptedPayload = struct.pack( '>BH', seqNo, messageType )
        if payload:
            encryptedPayload += payload
        crc = crc16.crc16xmodem( encryptedPayload, 0xffff )
        encryptedPayload += struct.pack( '>H', crc & 0xffff )
        # logger.debug("### PAYLOAD")
        # logger.debug(binascii.hexlify( encryptedPayload ))
        
        mmPayload = struct.pack( '<QBBB',
            self.session.pumpMAC,
            self.session.minimedSequenceNumber,
            0x11, # Mode flags
            len( encryptedPayload )
        )        
        mmPayload += self.encrypt( encryptedPayload )

        self.setPayload( mmPayload )
        self.session.sendSequenceNumber += 1

class MedtronicReceiveMessage( MedtronicMessage ):
    @classmethod
    def decode( cls, message, session ):
        response = MedtronicMessage.decode( message, session )
       
        # TODO - check validity of the envelope
        response.responseEnvelope = response.payload[0:22] 
        decryptedResponsePayload = response.decrypt( bytes(response.payload[22:]) )

        response.responsePayload = decryptedResponsePayload[0:-2]

        # logger.debug("### DECRYPTED PAYLOAD:")
        # logger.debug(binascii.hexlify( response.responsePayload ))

        if len( response.responsePayload ) > 2:
            checksum = struct.unpack( '>H', decryptedResponsePayload[-2:])[0]
            calcChecksum = MedtronicMessage.calculateCcitt( response.responsePayload )
            if( checksum != calcChecksum ):
                logger.info("ChecksumException2.")
                raise ChecksumException( 'Expected to get {0}. Got {1}'.format( calcChecksum, checksum ) )

        response.__class__ = MedtronicReceiveMessage
        
        if response.messageType == COM_D_COMMAND.TIME_RESPONSE:
            response.__class__ = PumpTimeResponseMessage
        elif response.messageType == COM_D_COMMAND.READ_HISTORY_INFO_RESPONSE:
            response.__class__ = PumpHistoryInfoResponseMessage
        elif response.messageType == COM_D_COMMAND.READ_PUMP_STATUS_RESPONSE:
            response.__class__ = PumpStatusResponseMessage
        elif response.messageType == COM_D_COMMAND.INITIATE_MULTIPACKET_TRANSFER:
            response.__class__ = MultiPacketSegment
        elif response.messageType == COM_D_COMMAND.MULTIPACKET_SEGMENT_TRANSMISSION:
            response.__class__ = MultiPacketSegment
        elif response.messageType == COM_D_COMMAND.END_HISTORY_TRANSMISSION:
            response.__class__ = MultiPacketSegment
        
        return response

    @property
    def messageType( self ):
        return struct.unpack( '>H', self.responsePayload[1:3] )[0]


class ReadInfoResponseMessage( object ):
    @classmethod
    def decode( cls, message ):
        response = cls()
        response.responsePayload = message
        return response

    @property
    def linkMAC( self ):
        return struct.unpack( '>Q', self.responsePayload[0:8] )[0]

    @property
    def pumpMAC( self ):
        return struct.unpack( '>Q', self.responsePayload[8:16] )[0]

class ReadLinkKeyResponseMessage( object ):
    @classmethod
    def decode( cls, message ):
        response = cls()
        response.responsePayload = message
        return response

    @property
    def packedLinkKey( self ):
        return struct.unpack( '>55s', self.responsePayload[0:55] )[0]

    def linkKey( self, serialNumber ):
        key = bytearray(b"")
        pos = ord_hack( serialNumber[-1:] ) & 7
        
        for it in range(16):
            if ( ord_hack( self.packedLinkKey[pos + 1] ) & 1) == 1:
                key.append(~ord_hack( self.packedLinkKey[pos] ) & 0xff)
            else:
                key.append(self.packedLinkKey[pos])

            if (( ord_hack( self.packedLinkKey[pos + 1] ) >> 1 ) & 1 ) == 0:
                pos += 3
            else:
                pos += 2

        return key

class PumpTimeResponseMessage( MedtronicReceiveMessage ):
    @classmethod
    def decode( cls, message, session ):
        response = MedtronicReceiveMessage.decode( message, session )
        if response.messageType != COM_D_COMMAND.TIME_RESPONSE:
            logger.warning("UnexpectedMessageException.") 
            UnexpectedMessageException( "Expected to get a Time Response message '{0}'. Got {1}.".format( COM_D_COMMAND.TIME_RESPONSE, response.messageType ) )

        # Since we only add behaviour, we can cast this class to ourselves
        response.__class__ = PumpTimeResponseMessage
        return response

    @property
    def timeSet( self ):
        if self.responsePayload[3] == 0:
            return False
        else:
            return True

    @property
    def encodedDatetime( self ):
        return struct.unpack( '>Q', self.responsePayload[4:] )[0]

    @property
    def datetime( self ):
        dateTimeData = self.encodedDatetime
        return DateTimeHelper.decodeDateTime( dateTimeData )

    @property
    def offset( self ):
        dateTimeData = self.encodedDatetime
        return DateTimeHelper.decodeDateTimeOffset( dateTimeData )


class PumpHistoryInfoResponseMessage( MedtronicReceiveMessage ):
    @classmethod
    def decode( cls, message, session ):
        response = MedtronicReceiveMessage.decode( message, session )
        if response.messageType != COM_D_COMMAND.READ_HISTORY_INFO_RESPONSE:
            logger.info("UnexpectedMessageException2.") 
            raise UnexpectedMessageException( "Expected to get a Time Response message '{0}'. Got {1}.".format( COM_D_COMMAND.READ_HISTORY_INFO_RESPONSE, response.messageType ) )
        # Since we only add behaviour, we can cast this class to ourselves
        response.__class__ = PumpHistoryInfoResponseMessage
        return response

    @property
    def historySize( self ):
        return struct.unpack( '>I', self.responsePayload[4:8] )[0]
    
    @property
    def encodedDatetimeStart( self ):
        return struct.unpack( '>Q', self.responsePayload[8:16] )[0]

    @property
    def encodedDatetimeEnd( self ):
        return struct.unpack( '>Q', self.responsePayload[16:24] )[0]    

    @property
    def datetimeStart( self ):
        dateTimeData = self.encodedDatetimeStart
        return DateTimeHelper.decodeDateTime( dateTimeData )

    @property
    def datetimeEnd( self ):
        dateTimeData = self.encodedDatetimeEnd
        return DateTimeHelper.decodeDateTime( dateTimeData )

class MultiPacketSegment( MedtronicReceiveMessage ):
    @classmethod
    def decode( cls, message, session ):
        response = MedtronicReceiveMessage.decode( message, session )
        # Since we only add behaviour, we can cast this class to ourselves
        response.__class__ = MultiPacketSegment
        return response

    @property
    def packetNumber( self ):
        return struct.unpack( '>H', self.responsePayload[3:5] )[0]
    
    @property
    def payload( self ):
        return self.responsePayload[5:]

    @property
    def segmentSize( self ):
        return struct.unpack( '>I', self.responsePayload[3:7] )[0]

    @property
    def packetSize( self ):
        return struct.unpack( '>H', self.responsePayload[7:9] )[0]

    @property
    def lastPacketSize( self ):
        return struct.unpack( '>H', self.responsePayload[9:11] )[0]

    @property
    def packetsToFetch( self ):
        return struct.unpack( '>H', self.responsePayload[11:13] )[0]

class PumpStatusResponseMessage( MedtronicReceiveMessage ):
    MMOL = 1
    MGDL = 2

    @classmethod
    def decode( cls, message, session ):
        response = MedtronicReceiveMessage.decode( message, session )
        if response.messageType != COM_D_COMMAND.READ_PUMP_STATUS_RESPONSE:
            logger.info("UnexpectedMessageException3.") 
            raise UnexpectedMessageException( "Expected to get a Status Response message '{0}'. Got {1}.".format( COM_D_COMMAND.READ_PUMP_STATUS_RESPONSE, response.messageType ) )

        # Since we only add behaviour, we can cast this class to ourselves
        response.__class__ = PumpStatusResponseMessage
        return response

    @property
    def currentBasalRate( self ):
        return float( struct.unpack( '>I', self.responsePayload[0x1b:0x1f] )[0] ) / 10000

    @property
    def tempBasalRate( self ):
        return float( struct.unpack( '>H', self.responsePayload[0x21:0x23] )[0] ) / 10000

    @property
    def tempBasalPercentage( self ):
        return int( struct.unpack( '>B', self.responsePayload[0x23:0x24] )[0] )

    @property
    def tempBasalMinutesRemaining( self ):
        return int( struct.unpack( '>H', self.responsePayload[0x24:0x26] )[0] )

    @property
    def batteryLevelPercentage( self ):
        return int( struct.unpack( '>B', self.responsePayload[0x2a:0x2b] )[0] )

    @property
    def insulinUnitsRemaining( self ):
        return int( struct.unpack( '>I', self.responsePayload[0x2b:0x2f] )[0] ) / 10000

    @property
    def activeInsulin( self ):
        return ( struct.unpack( '>H', self.responsePayload[51:53] )[0] ) / 10000

    @property
    def sensorBGL( self ):
        return int( struct.unpack( '>H', self.responsePayload[53:55] )[0] )

    @property
    def trendArrow( self ):
        status = int( struct.unpack( '>B', self.responsePayload[0x40:0x41] )[0] )
        if status == 0x60:
            return "No arrows"
        elif status == 0xc0:
            return "3 arrows up"
        elif status == 0xa0:
            return "2 arrows up"
        elif status == 0x80:
            return "1 arrow up"
        elif status == 0x40:
            return "1 arrow down"
        elif status == 0x20:
            return "2 arrows down"
        elif status == 0x00:
            return "3 arrows down"
        else:
            return "Unknown trend"

    @property
    def sensorBGLTimestamp( self ):
        dateTimeData = struct.unpack( '>Q', self.responsePayload[55:63] )[0]
        return DateTimeHelper.decodeDateTime( dateTimeData )

    @property
    def recentBolusWizard( self ):
        if self.responsePayload[72] == 0:
            return False
        else:
            return True

    @property
    def bolusWizardBGL( self ):
        return struct.unpack( '>H', self.responsePayload[73:75] )[0]

class BeginEHSMMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        payload = struct.pack( '>B', 0x00 )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.HIGH_SPEED_MODE_COMMAND, session, payload )

class FinishEHSMMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        payload = struct.pack( '>B', 0x01 )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.HIGH_SPEED_MODE_COMMAND, session, payload )

class PumpTimeRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.TIME_REQUEST, session )

class PumpStatusRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.READ_PUMP_STATUS_REQUEST, session )

class PumpHistoryInfoRequestMessage( MedtronicSendMessage ):
    def __init__( self, session, dateStart, dateEnd, dateOffset, requestType = HISTORY_DATA_TYPE.PUMP_DATA):
        histDataType_PumpData = requestType
        fromRtc = DateTimeHelper.rtcFromDate(dateStart, dateOffset)
        toRtc = DateTimeHelper.rtcFromDate(dateEnd, dateOffset)
        payload = struct.pack( '>BBIIH', histDataType_PumpData, 0x04, fromRtc, toRtc, 0x00 )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.READ_HISTORY_INFO_REQUEST, session, payload )

class PumpHistoryRequestMessage( MedtronicSendMessage ):
    def __init__( self, session, dateStart, dateEnd, dateOffset, requestType = HISTORY_DATA_TYPE.PUMP_DATA ):
        histDataType_PumpData = requestType
        fromRtc = DateTimeHelper.rtcFromDate(dateStart, dateOffset)
        toRtc = DateTimeHelper.rtcFromDate(dateEnd, dateOffset)
        payload = struct.pack( '>BBIIH', histDataType_PumpData, 0x04, fromRtc, toRtc, 0x00 )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.READ_HISTORY_REQUEST, session, payload )

class AckMultipacketRequestMessage( MedtronicSendMessage ):
    SEGMENT_COMMAND__INITIATE_TRANSFER = COM_D_COMMAND.INITIATE_MULTIPACKET_TRANSFER
    SEGMENT_COMMAND__SEND_NEXT_SEGMENT = COM_D_COMMAND.MULTIPACKET_SEGMENT_TRANSMISSION
    
    def __init__( self, session, segmentCommand ):
        payload = struct.pack( '>H', segmentCommand )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.ACK_MULTIPACKET_COMMAND, session, payload )
    
class BasicNgpParametersRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.NGP_PARAMETER_REQUEST, session )

class DeviceCharacteristicsRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.DEVICE_CHARACTERISTICS_REQUEST, session )

class SuspendResumeRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        # TODO: Bug? Shall the payload be passed to the message, or not needed?
        payload = struct.pack( '>B', 0x01 )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.SUSPEND_RESUME_REQUEST, session )

class PumpTempBasalRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.TEMP_BASAL_REQUEST, session )

class PumpBolusesRequestMessage( MedtronicSendMessage ):
    def __init__( self, session ):
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.BOLUSES_REQUEST, session )

class PumpRemoteBolusRequestMessage( MedtronicSendMessage ):
    def __init__( self, session, bolusID, amount, execute ):
        unknown1 = 0 # ??
        unknown2 = 0 # Square Wave amount?
        unknown3 = 0 # Square Wave length?
        payload = struct.pack( '>BBHHBH', bolusID, execute, unknown1, amount * 10000, unknown2, unknown3 )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.REMOTE_BOLUS_REQUEST, session, payload )

class Type405RequestMessage( MedtronicSendMessage ):
    def __init__( self, session, pumpDateTime ):
        payload = struct.pack( '>BQ', 0x01, pumpDateTime )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.REQUEST_0x0405, session, payload )

class Type124RequestMessage( MedtronicSendMessage ):
    def __init__( self, session, pumpDateTime ):
        payload = struct.pack( '>QBB', pumpDateTime, 0x00, 0xFF )
        MedtronicSendMessage.__init__( self, COM_D_COMMAND.REQUEST_0x0124, session, payload )

class BayerBinaryMessage( object ):
    def __init__( self, messageType=None, session=None, payload=None ):
        self.payload = payload
        self.session = session
        if messageType and self.session:
            self.envelope = struct.pack( '<BB6s10sBI5sI', 0x51, 3, b'000000', b'\x00' * 10,
                messageType, self.session.bayerSequenceNumber, b'\x00' * 5, 
                len( self.payload ) if self.payload else 0 )
            self.envelope += struct.pack( 'B', self.makeMessageCrc() )

    def makeMessageCrc( self ):
        checksum = 0
        for x in self.envelope[0:32]:
            checksum += ord_hack(x)
        #checksum = sum( bytearray(self.envelope[0:32], 'utf-8') )

        if self.payload:
            checksum += sum( bytearray( self.payload ) )

        return checksum & 0xff

    def encode( self ):
        # Increment the Bayer Sequence Number
        self.session.bayerSequenceNumber += 1
        if self.payload:
            return self.envelope + self.payload
        else:
            return self.envelope

    @classmethod
    def decode( cls, message ):
        response = cls()
        response.envelope = message[0:33]
        response.payload = message[33:] 

        checksum = message[32]
        calcChecksum = response.makeMessageCrc()
        if( checksum != calcChecksum ):
            logger.error('ChecksumException: Expected to get {0}. Got {1}'.format( calcChecksum, checksum ))
            raise ChecksumException( 'Expected to get {0}. Got {1}'.format( calcChecksum, checksum ) )

        return response
    
    @property
    def linkDeviceOperation( self ):
        return ord_hack(self.envelope[18])

    # HACK: This is just a debug try, session param shall not be there    
    def checkLinkDeviceOperation( self, expectedValue, session = None ):
        if self.linkDeviceOperation != expectedValue:
            logger.debug("### checkLinkDeviceOperation BayerBinaryMessage.envelope: {0}".format(binascii.hexlify(self.envelope)))
            logger.debug("### checkLinkDeviceOperation BayerBinaryMessage.payload: {0}".format(binascii.hexlify(self.payload)))
            # HACK: This is just a debug try
            if self.linkDeviceOperation == 0x80:
                response = MedtronicReceiveMessage.decode( self.payload, session )
                ("#### Message type of caught 0x80: 0x{0:x}".format(response.messageType))
            raise UnexpectedMessageException( "Expected to get linkDeviceOperation {0:x}. Got {1:x}".format( expectedValue, self.linkDeviceOperation ) )

class Medtronic600SeriesDriver( object ):
    USB_BLOCKSIZE = 64
    USB_VID = 0x1a79
    USB_PID = 0x6210
    MAGIC_HEADER = b'ABC'

    CHANNELS = [ 0x14, 0x11, 0x0e, 0x17, 0x1a ] # In the order that the CareLink applet requests them

    session = None
    offset = -1592387759; # Just read out of my pump. Shall be overwritten by reading date/time from pump

    def __init__( self ):
        self.session = MedtronicSession()
        self.device = None

        self.deviceInfo = None

    def openDevice( self ):
        logger.info("Opening device")
        global noCNL
        try:
            self.device = hid.device()
            self.device.open( self.USB_VID, self.USB_PID )
            logger.info("Manufacturer: %s" % self.device.get_manufacturer_string())
            logger.info("Product: %s" % self.device.get_product_string())
            logger.info("Serial No: %s" % self.device.get_serial_number_string())
        except:
            logger.info("OpenDevice-NotOpening - CNL not connected to USB Port")
            noCNL = 1 #CNL not connected (or reading correctly)

    def closeDevice( self ):
        logger.info("# Closing device")
        self.device.close()

    def readMessage( self ):
        payload = bytearray()
        while True:
            data = self.device.read( self.USB_BLOCKSIZE, timeout_ms = 10000 )
            if data:
                if( bytearray( data[0:3] ) != self.MAGIC_HEADER ):
                    logger.error('Recieved invalid USB packet')
                    raise RuntimeError( 'Recieved invalid USB packet')
                payload.extend( data[4:data[3] + 4] )
                # TODO - how to deal with messages that finish on the boundary?
                if data[3] != self.USB_BLOCKSIZE - 4:
                    break
            else:
                logger.warning('Timeout waiting for message')
                raise TimeoutException( 'Timeout waiting for message' )

        # logger.debug("READ: " + binascii.hexlify( payload )) # Debugging
        return payload

    def sendMessage( self, payload ):
        # Split the message into 60 byte chunks
        for packet in [ payload[ i: i+60 ] for i in range( 0, len( payload ), 60 ) ]:
            message = struct.pack( '>3sB', self.MAGIC_HEADER, len( packet ) ) + packet
            self.device.write( bytearray( message ) )
            # logger.debug("SEND: " + binascii.hexlify( message )) # Debugging

    @property
    def deviceSerial( self ):
        if not self.deviceInfo:
            return None
        else:
            return self.deviceInfo[0][4][3][1]

    def getDeviceInfo( self ):
        logger.info("# Reading Device Info")
        self.sendMessage( struct.pack( '>B', 0x58 ) )

        try:
            msg = self.readMessage()

            if not astm.codec.is_chunked_message( msg ):
                logger.error('readDeviceInfo: Expected to get an ASTM message, but got {0} instead'.format( binascii.hexlify( msg ) ))
                raise RuntimeError( 'Expected to get an ASTM message, but got {0} instead'.format( binascii.hexlify( msg ) ) )

            self.deviceInfo = astm.codec.decode( bytes( msg ) )
            self.session.stickSerial = self.deviceSerial
            self.checkControlMessage( ascii['ENQ'] )

        except TimeoutException:
            logger.info("getDeviceInfo TimeoutException.") 
            self.sendMessage( struct.pack( '>B', ascii['EOT'] ) )
            self.checkControlMessage( ascii['ENQ'] ) ## this happens when cnl read attempted soon after cnl is plugged into usb
            self.getDeviceInfo()

    def checkControlMessage( self, controlChar ):
        msg = self.readMessage()
        if len( msg ) > 0 and msg[0] != controlChar: #this happens if cnl read attempted soon after cnl is plugged into usb
            logger.error(' ### checkControlMessage: Expected to get an 0x{0:x} control character, got message with length {1} and control char 0x{1:x}'.format( controlChar, len( msg ), msg[0] ))
            raise RuntimeError( 'Expected to get an 0x{0:x} control character, got message with length {1} and control char 0x{1:x}'.format( controlChar, len( msg ), msg[0] ) )

    def enterControlMode( self ):
        logger.info("# enterControlMode")
        self.sendMessage( struct.pack( '>B', ascii['NAK'] ) )
        self.checkControlMessage( ascii['EOT'] )
        self.sendMessage( struct.pack( '>B', ascii['ENQ'] ) )
        self.checkControlMessage( ascii['ACK'] )

    def exitControlMode( self ):
        logger.info("# exitControlMode")
        try:
            self.sendMessage( struct.pack( '>B', ascii['EOT'] ) )
            self.checkControlMessage( ascii['ENQ'] )
        except Exception:
            logger.warning("Unexpected error by exitControlMode, ignoring", exc_info = True);

    def enterPassthroughMode( self ):
        logger.info("# enterPassthroughMode")
        self.sendMessage( struct.pack( '>2s', b'W|' ) )
        self.checkControlMessage( ascii['ACK'] )
        self.sendMessage( struct.pack( '>2s', b'Q|' ) )
        self.checkControlMessage( ascii['ACK'] )
        self.sendMessage( struct.pack( '>2s', b'1|' ) )
        self.checkControlMessage( ascii['ACK'] )

    def exitPassthroughMode( self ):
        logger.info("# exitPassthroughMode")
        try:
            self.sendMessage( struct.pack( '>2s', b'W|' ) )
            self.checkControlMessage( ascii['ACK'] )
            self.sendMessage( struct.pack( '>2s', b'Q|' ) )
            self.checkControlMessage( ascii['ACK'] )
            self.sendMessage( struct.pack( '>2s', b'0|' ) )
            self.checkControlMessage( ascii['ACK'] )
        except Exception:
            logger.warning("Unexpected error by exitPassthroughMode, ignoring", exc_info = True);

    def openConnection( self ):
        logger.info("# Request Open Connection")

        mtMessage = binascii.unhexlify( self.session.HMAC )
        bayerMessage = BayerBinaryMessage( 0x10, self.session, mtMessage )
        self.sendMessage( bayerMessage.encode() )
        self.readMessage()

    def closeConnection( self ):
        logger.info("# Request Close Connection")
        try:
            mtMessage = binascii.unhexlify( self.session.HMAC )
            bayerMessage = BayerBinaryMessage( 0x11, self.session, mtMessage )
            self.sendMessage( bayerMessage.encode() )
            self.readMessage()
        except Exception:
            logger.warning("Unexpected error by requestCloseConnection, ignoring", exc_info = True);

    def readInfo( self ):
        logger.info("# Request Read Info")
        bayerMessage = BayerBinaryMessage( 0x14, self.session )
        self.sendMessage( bayerMessage.encode() )
        response = BayerBinaryMessage.decode( self.readMessage() ) # The response is a 0x14 as well
        info = ReadInfoResponseMessage.decode( response.payload )
        self.session.linkMAC = info.linkMAC
        self.session.pumpMAC = info.pumpMAC

    def readLinkKey( self ):
        logger.info("# Request Read Link Key")
        bayerMessage = BayerBinaryMessage( 0x16, self.session )
        self.sendMessage( bayerMessage.encode() )
        response = BayerBinaryMessage.decode( self.readMessage() ) # The response is a 0x14 as well
        keyRequest = ReadLinkKeyResponseMessage.decode( response.payload )
        self.session.KEY = bytes(keyRequest.linkKey( self.session.stickSerial ))
        logger.debug("LINK KEY: {0}".format(binascii.hexlify(self.session.KEY)))


    def negotiateChannel( self ):
        logger.info("# Negotiate pump comms channel")

        # Scan the last successfully connected channel first, since this could save us negotiating time
        for self.session.radioChannel in [ self.session.config.lastRadioChannel ] + self.CHANNELS:
            logger.debug("Negotiating on channel {0}".format( self.session.radioChannel ))

            mtMessage = ChannelNegotiateMessage( self.session )

            bayerMessage = BayerBinaryMessage( 0x12, self.session, mtMessage.encode() )
            self.sendMessage( bayerMessage.encode() )
            self.getBayerBinaryMessage(0x81) # Read the 0x81
            response = BayerBinaryMessage.decode( self.readMessage() ) # Read the 0x80
            if len( response.payload ) > 13:
                # Check that the channel ID matches
                responseChannel = response.payload[43]
                if self.session.radioChannel == responseChannel:
                    break
                else:
                    raise UnexpectedMessageException( "Expected to get a message for channel {0}. Got {1}".format( self.session.radioChannel, responseChannel ) )
            else:
                self.session.radioChannel = None

        if not self.session.radioChannel:
            raise NegotiationException( 'Could not negotiate a comms channel with the pump. Are you near to the pump?' )
        else:
            self.session.config.lastRadioChannel = self.session.radioChannel

    def beginEHSM( self ):
        logger.info("# Begin Extended High Speed Mode Session")
        mtMessage = BeginEHSMMessage( self.session )

        bayerMessage = BayerBinaryMessage( 0x12, self.session, mtMessage.encode() )
        self.sendMessage( bayerMessage.encode() )
        self.getBayerBinaryMessage(0x81) # The Begin EHSM only has an 0x81 response.

    def finishEHSM( self ):
        logger.info("# Finish Extended High Speed Mode Session")
        try:
            mtMessage = FinishEHSMMessage( self.session )
    
            bayerMessage = BayerBinaryMessage( 0x12, self.session, mtMessage.encode() )
            self.sendMessage( bayerMessage.encode() )
            try:
                self.getBayerBinaryMessage(0x81) # The Finish EHSM only has an 0x81 response.
            except:
                # if does not come, ignore...
                pass
        except Exception:
            logger.warning("Unexpected error by finishEHSM, ignoring", exc_info = True);

    def getBayerBinaryMessage(self, expectedLinkDeviceOperation):
        messageReceived = False
        message = None
        while messageReceived == False:
            message = BayerBinaryMessage.decode(self.readMessage())
            if message.linkDeviceOperation == expectedLinkDeviceOperation:
                messageReceived = True
            else:
                logger.warning("## getBayerBinaryMessage: waiting for message 0x{0:x}, got 0x{1:x}".format(expectedLinkDeviceOperation, message.linkDeviceOperation))
        return message

    def getMedtronicMessage(self, expectedMessageTypes):
        messageReceived = False
        medMessage = None
        while messageReceived == False:
            message = self.getBayerBinaryMessage(0x80)
            medMessage = MedtronicReceiveMessage.decode(message.payload, self.session)
            if medMessage.messageType in expectedMessageTypes:
                messageReceived = True
            else:
                logger.warning("## getMedtronicMessage: waiting for message of [{0}], got 0x{1:x}".format(''.join('%04x '%i for i in expectedMessageTypes) , medMessage.messageType))
        return medMessage

    def getPumpTime( self ):
        logger.info("# Get Pump Time")
        mtMessage = PumpTimeRequestMessage( self.session )

        bayerMessage = BayerBinaryMessage( 0x12, self.session, mtMessage.encode() )
        self.sendMessage( bayerMessage.encode() )
        self.readMessage() # Read the 0x81
        response = BayerBinaryMessage.decode( self.readMessage() ) # Read the 0x80
        return PumpTimeResponseMessage.decode( response.payload, self.session ).datetime

    def getPumpStatus( self ):
        logger.info("# Get Pump Status")
        mtMessage = PumpStatusRequestMessage( self.session )

        bayerMessage = BayerBinaryMessage( 0x12, self.session, mtMessage.encode() )
        self.sendMessage( bayerMessage.encode() )
        self.getBayerBinaryMessage(0x81) # Read the 0x81
        response = self.getMedtronicMessage([COM_D_COMMAND.READ_PUMP_STATUS_RESPONSE])
        return response

# Deleted unused defs for History, unneeded for display/alarm

def downloadPumpSession(downloadOperations):
    global realpumptime 
    global noCNL
    mt = Medtronic600SeriesDriver()
    mt.openDevice()
    if noCNL == 0: #mt.openDevice in line above makes noCNL=1 now if it finds no CNL attached)
        try: 
            logger.info("TRY--mt.getDeviceInfo,mt.enterControlMode()")
            mt.getDeviceInfo() #when cnl starting up after plugged into usb, this is where program does runtime failure and then does belowmost 'finally' before exiting
            logger.info("Device serial: {0}".format(mt.deviceSerial))
            mt.enterControlMode()
            try: 
                logger.info("TRY--enterPassthroughMode()")
                mt.enterPassthroughMode()
                try: 
                    logger.info("TRY--mt.openConnection()")
                    mt.openConnection()
                    try:
                        logger.info("TRY--mt.readInfo,LinkKey,beginEHSM()")
                        mt.readInfo()
                        mt.readLinkKey()
                        try:
                            logger.info("   TRY--mt.negotiateChannel()")
                            mt.negotiateChannel()
                        except:
                            logger.warning("   EXCEPTION--mt.negotiateChannel()")  ## TESTING -- failed here, then goes where?
                            noCNL = 1
                            return
                        mt.beginEHSM()
                        try:  
                            logger.info("   TRY--mt.GetPumpTime()")
                            # We need to read always the pump time to store the offset for later messeging
                            realpumptime = mt.getPumpTime()
                            print('\n' + 'Pumptime: {0}').format(realpumptime) 
                            try:
                                logger.info("      TRY--downloadOperations(mt)") 
                                downloadOperations(mt)
                            except Exception:
                                logger.warning("      EXCEPT--downloadOperations(mt)", exc_info = True)
                                noCNL = 1 
                                raise
                        except Exception: 
                            logger.warning("   EXCEPT--downloadOperations(mt) -- CANT GET REALPUMPTIME", exc_info = True)
                            noCNL = 1
                        finally:
                            logger.info("   FINALLY-after--mt.GetPumpTime()")
                            mt.finishEHSM()
                    finally:
                        logger.info("FINALLY-after--mt.readInfo,LinkKey,beginEHSM().  Running mt.closeConnection") 
                        mt.closeConnection()
                finally: 
                    logger.info("FINALLY-after--mt.openConnection().  Running mt.exitPassthroughMode")
                    mt.exitPassthroughMode()
            finally:
                logger.info("FINALLY-after--enterPassthroughMode().  Running mt.exitControlMode")
                mt.exitControlMode()
        except Exception: #for failed mt.getDeviceInfo() - ie, when CNL is still starting up when CNL tries to read and fails read.  
            logger.warning("EXCEPT--mt.getDeviceInfo().  Closing Device Next..")
            logger.warning("   (EXCEPTON from somewhere! Seeting noCNL=1")
            print("EXCEPTION from somewhere!  Setting noCNL=1")
            noCNL = 1                      
        finally:
            logger.info("FINALLY-after--mt.getDeviceInfo,mt.enterControlMode() - Running mt.closeDevice")
            mt.closeDevice()
    else:
        logger.info("ELSE--No CNL connected to USB, returning to main")
        print("No CNL connected to USB. Returning!")
        return

def pumpDownload(mt):
    global BGLtime
    global BGL
    status = mt.getPumpStatus()
    print ("MY PUMP INFO - ")
    print ("Active Insulin: {0:.3f}U".format( status.activeInsulin ))
    print ("Sensor BGL: {0} mg/dL ({1:.1f} mmol/L) at {2}".format( status.sensorBGL,
             status.sensorBGL / 18.016,
             status.sensorBGLTimestamp.strftime( "%c" ) ))
    print ("BGL trend: {0}".format( status.trendArrow ))
    print ("Current basal rate: {0:.3f}U".format( status.currentBasalRate ))
    print ("Temp basal rate: {0:.3f}U".format( status.tempBasalRate ))
    print ("Temp basal percentage: {0}%".format( status.tempBasalPercentage ))
    print ("Units remaining: {0:.3f}U".format( status.insulinUnitsRemaining ))
    print ("Battery remaining: {0}%".format( status.batteryLevelPercentage ))

    BGLtime = status.sensorBGLTimestamp
    BGL = status.sensorBGL
    logger.info("     BGLtime: {0}".format( BGLtime )) 
    logger.info("End of pump download") 

if __name__ == '__main__': 
    IO.setwarnings(False)  #Comment this line out to see GPIO warnings
    IO.setmode(IO.BCM)
    Display = tm1637.TM1637(CLK=23,DIO=24,brightness=1.0) #Configure Display wiring and brightness(0/off-1.0/full brightness)
    IO.setup(26, IO.IN, pull_up_down=IO.PUD_UP) #Configure pushbutton wiring and resistors
    IO.setup(17,IO.OUT) #Configure Buzzer wiring
    Display.Clear()
    for i in range(1,20): #blink the Heartbeat, depending on current value
        Display.Show1(0,39) #display 1st char_HB
        time.sleep(0.5)
        Display.Show1(0,36) #hide 1st char_HB
        time.sleep(0.5)
    noCNLcounter = 0
    noSigcounter = 0
    SnoozeActive = 0
    SnoozePBcheckON = 0
    display_char0lwr = 0 #display current status: 0 = blank, 1 = upper circle, 2 = 8
    BGLnoSig = 0 # to update display correctly when pump shows no Signal
    timetodelay = 0 #Start with no time delay for 1st CNL read
    BGLLowBuzzerReq = 0 #Start with buzzer off
    starttime = time.time() #time CNL was last updated (or time it was turned on if CNL not read yet)
    startCNLcheck = 1 #1 means CNL delay done, ready to do next (or initial) CNL check

    while True:
        # See if delay to next CNL check is done/ready to check CNL again
        print("Next CNLcheck check when: {:.1f} >= {:.1f}").format((time.time() - starttime), timetodelay)
        if ((time.time() - starttime) >= timetodelay):
            startCNLcheck = 1
            print('startCNLcheck set to 1.')
        # Ring Buzzer
        if (BGLLowBuzzerReq == 1):  #Buzzer being requested on (CNL says BGL low)
            # If Snooze is NOT active (and has been requested on by CNL BGL low)
            if SnoozeActive == 0:
                print('CNL says BGLLow.  SnoozeActive currently == 0 (alarm snooze OFF)')
                # Turn on PB Snooze Button Checking if not already on
                if SnoozePBcheckON == 0:
                    IO.add_event_detect(26, IO.RISING) # Start detecting Snooze PB input
                    SnoozePBcheckON = 1
                    print('SnoozePBcheckON == 1 now.')
                # Start beeping buzzer (4x)
                print('Alarm Buzzing...')
                for i in range(0, 3):
                    IO.output(17,IO.HIGH)
                    sleep(0.5)
                    IO.output(17,IO.LOW)
                    sleep(0.5)
                # Check if Snooze PB has been pressed (since buzzing started and snooze not active yet)
                if IO.event_detected(26):
                    print('Snooze PB was pressed.')
                    snoozestart = time.time() # Set snooze start time
                    print('snoozestart = {}s').format(snoozestart)
                    SnoozeActive = 1 # Set snooze status as active
                    print('SnoozeActive == 1 now.')
                    # Stop detecting if Snooze PB is pressed
                    IO.remove_event_detect(26)
                    SnoozePBcheckON = 0
                    print('SnoozePBcheckON == 0 now.')
            # If Snooze IS active
            else: # Check whether snooze time is up and it should be deactivated
                print('CNL says BGLLow.  SnoozeActive currently == 1 (alarm snooze ON)')
                print ("Snooze over when diff({}s) >= SnoozeTimeSP({}s))").format( time.time()-snoozestart, SnoozeTimeSP*60)
                if ((time.time() - snoozestart) >= (SnoozeTimeSP * 60)): #snooze has been active for snoozeSP time
                    SnoozeActive = 0 # Set snooze status as inactive
                    print('Snooze time is over.  SnoozeActive == 0 now.')

        # Show watchdog/blinking underscore in char1 display, to indicate that the display/program is still updating/running
        if display_char0lwr == 0: # currently: blank
            displayHB = 39   # heartbeat: blank + underscore
            displayNoHB = 36
        elif display_char0lwr == 1: # currently: upper circle
            displayHB = 42   # heartbeat: upper circle + underscore
            displayNoHB = 41
        elif display_char0lwr == 2: # currently: 8
            displayHB = 43   # heartbeat: 8 + + NOunderscore
            displayNoHB = 8

        for i in range(1,4): #blink the Heartbeat, depending on current value
            Display.Show1(0,displayHB) #displayHB
            time.sleep(0.125)
            Display.Show1(0,displayNoHB) #display current/displayNoHB
            time.sleep(0.125)
            Display.Show1(0,displayHB) #displayHB
            time.sleep(0.125)
            Display.Show1(0,displayNoHB) #display current/displayNoHB
            time.sleep(0.5)
        if display_char0lwr == 1:
            Display.Show1(0,displayHB) #displayHB
            
        #CNL - connection attempt/download info from pump
        if ((noCNL == 0) and (startCNLcheck == 1)): #First time running or CNL is confirmed attached, and delay done/ready for next CNLcheck
            print('CNLCheck Starting (noCNL==0, starCBLcheck==1)...  ')              
            downloadPumpSession(pumpDownload)  #Connects (will set noCNL=1 if CNL not attached), Reads/decodes CNL data, Closes, Prints CNL data

            print('CNLCheck Done.  noCNL = {0}.  (0 means CNL attached or BGL updated.  1 means not attached or BGL not updated)').format(noCNL)
            logger.info('CNLCheck Done.  noCNL = %d.  (0 means CNL attached.  1 means not attached)', noCNL )

            # If CNL confirmed attached and with no download Exceptions/read data OK (from downloadPumpSession in line above)
            if noCNL == 0:
                # Calculate when to do next BG check
                try: #in case CNL read fails and realpumptime is still null
                    timediff = int((realpumptime - BGLtime).seconds)
                    timetodelay = ((5.25*60) - timediff) #will do next BG check in 5.25min minus the time since the last BGreading
                    print ("   TIMEDIFF between pumptime and BGLtime: {0:.1f}sec").format(timediff) 
                    print ("   TIMEDIFF between pumptime and BGLtime: {0:.1f}min").format(timediff/60.00) 
                    print ("   Delay to next read: 5.25min-TIMEDIFF = {0:.1f}min ({1:.1f}s)...").format((timetodelay/60.00),timetodelay) 
                except Exception: #Shouldn't need this
                    logger.warning("Pumpdelay calc Exception - Setting next CNL delay to 180s")
                    print ("\n")
                    print ("realpumptime: {0}").format(realpumptime)
                    print ("timediff: {0}").format(timediff)
                    print ("timetodelay : {0}").format(timetodelay)
                    print ("Pumpdelay calc Exception.  Setting CNL update delay set to 180s"+"\n")
                    timetodelay = 180
                if (timetodelay < 0):  #when timediff is greater than negative, ie when xmitter had no signal and now back in range
                    timetodelay = 180

                # Update display
                noSigcounter = noSigcounter + 1
                #CNL reads BGL OK, but BGL=0 (this means no signal/red X on pump)
                if BGL < 1:
                    if (noSigcounter == 1 and noCNLcounter == 0): #noSigcounter=1 (about 5.25min older than sensor time) AND not already old data
                        Display.Show1(0,41) #display topcircle in 1st char (= 41)
                        display_char0lwr = 1
                    elif (noSigcounter == 3): #(about 11min than sensor time) AND not already old data
                        Display.Show1(0, 8) #Display: 1st digit
                        display_char0lwr = 2 #set to show that bottom underscore char of leftmost char (char0) is on
                    # Display 8888 (when CNL reads no signal for ~17min)
                    elif noSigcounter >= 5: #data stale or CNL detached for 17min
                        Display.Clear()
                        Display.Show1(3,8) #1st display digit - show 8 to show that somethings messed up/CNL not attached
                        Display.Show1(2,8) #2nd display digit - show 8 to show that somethings messed up/CNL not attached
                        Display.Show1(1,8) #3rd display digit - show 8 to show that somethings messed up/CNL not attached
                        Display.Show1(0,8) #4th display digit - show 8 to show that somethings messed up/CNL not attached
                        display_char0lwr = 2
                        noSigcounter = 20 #don't want it to contintually grow/get too big
                    print ("Display Updated - Pump has no sensor signal/redX - data old(display1stChar=circles) or stale(display=8888)")
                    logger.info("Display Updated - Pump has no sensor signal/redX - data olddisplay1stChar=circles) or stale(display=8888)")
                    BGLnoSig = 1 #so low alarm doesn't start buzzing
                elif BGL == 770: #CNL reads BGL OK, but BGL=770 (this means sensor is or needs calibrating)
                    noSigcounter = 0
                    BGLnoSig = 0 #reset
                    display_char0lwr = 0 
                    Display.Show1(1,12) #2nd display digit - show C
                    Display.Show1(2,10) #3rd display digit - show A
                    Display.Show1(3,21) #3rd display digit - show L
                    print ("Display Updated with CAL/Calibration required.")
                    logger.info("Display Updated with CAL/Calibration required")
                else: #CNL reads normal BGL (# not 0/no signal or 770/calibrating)
                    Display.Clear()
                    display_char0lwr = 0
                    noSigcounter = 0
                    BGLnoSig = 0 #reset
                    digits = list(map(int,' '.join(str(BGL)).split()))  #splits BGL into list of individual digits
                    BGLnumdigits = len(digits)
                    while len(digits) < 4:
                        digits = [0] + digits
                    Display.Show1(3, digits[3]) #Display: 4th digit (rightmost/ones digit)(position3)
                    Display.Show1(2, digits[2]) #Display: 3rd digit (2nd to last/tens digit)(position2)
                    if digits[1] != 0:
                            Display.Show1(1, digits[1]) #2nd display digit(position1), display value of digits[1], if not 0
                    print ("Display Updated with new BGL")
                    logger.info("Display Updated with new BGL")
                    
                # Set request to Activate or Deactivate Alarm/Buzzer (if BGL low or not)
                if (BGL < BGlowSP) and (BGLnoSig == 0):
                    BGLLowBuzzerReq = 1
                    print ("CNL says BGL LOW!")
                elif (BGL >= BGlowSP) and (BGLnoSig == 0): #if not low this scan
                    if SnoozeActive == 1:
                        SnoozeActive =0 #shutoff snooze
                    if SnoozePBcheckON == 1: #buzzer on
                        IO.remove_event_detect(26) #turnoff snooze pb check
                        SnoozePBcheckON = 0 #turnoff snooze status
                    BGLLowBuzzerReq = 0
                    print ("CNL says BGL NOT low")
                elif (BGLnoSig == 1):
                    print ("CNL says no xmitter signal")
                else:
                    print("Program messed up - FIX.")

                # Resets needed to restart countdown to next CNL/BGL check
                startCNLcheck = 0
                starttime = time.time() #reset Delay Counter to show that CNL just updated
                # Reset being data updated from CNL
                noCNLcounter = 0 #reset

            else: #noCNL = 1 (no CNL is attached, or noCNL was set to 1 because of an exception/did NOT read data for any reason) 
                print ("\n"+"CNL not attached or Exceptions occurred -- Could not read CNL/Pumpdata"+"\n")
                logger.info("CNL not attached or Exceptions occurred -- Could not read CNL/Pumpdata")

                noCNLcounter = noCNLcounter + 1
                noCNL = 0 #resets CNL to 1 so it can continue to do CNL (connection) check while CNL not plugged in (to see if it's since been plugged in.  if not plugged in will reset there back to 1)
                timetodelay = 15 # Delay time (sec) to next CNL check.  IF THIS IS CHANGED, also change when 8888 should be displayed below 
                startCNLcheck = 0 #resets needed to restart countdown to next CNL check
                starttime = time.time() #reset Delay Counter to show that CNL just attempted read/getting starttime of new countdown to next read
                print ("    So check for CNL connection will begin every 15 sec. And shut-off Buzzer, if active.")

                # Update display, when data old or no CNL attached
                if noCNLcounter == 1: #noCNLcounter=1 (often ~5.25min older than sensor time/1pump reading if an exception)
                    Display.Show1(0,41) #display topcircle in 1st char (= 41)
                    display_char0lwr = 1
                elif noCNLcounter == 16: #another 5min later (10.25min older than sensor time/2 pump readings )(each noCNLcounter = ~20s due to time the scan attempt takes (3/min))
                    Display.Show1(0, 8) #Display: 8 in 1st digit
                    display_char0lwr = 2
                # Display 8888
                elif noCNLcounter >= 32: #another 5min later data stale or CNL detached for ~15.25min
                    Display.Show1(3,8) #1st display digit - show 8 to show that something is messed up/CNL not attached
                    Display.Show1(2,8) #2nd display digit - show 8 to show that something is messed up/CNL not attached
                    Display.Show1(1,8) #3rd display digit - show 8 to show that something is messed up/CNL not attached
                    Display.Show1(0,8) #4th display digit - show 8 to show that something is messed up/CNL not attached
                    display_char0lwr = 2
                    noCNLcounter = 20 #don't want it to continually grow/get too big
                    # Deactivate snooze and low alarm after 15 min of stale data 
                    BGLLowBuzzerReq = 0 # deactivate 'CNL BGL low buzzer request'
                    SnoozeActive = 0 # Reset snooze status to inactive
                print ("Display Updated - data old - about: {0}s ({1}min)").format((noCNLcounter*28), ((noCNLcounter*28)/60))
                logger.info("Display Updated - data old - display1stChar=circles or display=8888 if older")
            #end of program
        #end of program
    #end of program
#end of program
