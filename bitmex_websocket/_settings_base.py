from os.path import join
import logging
import os

########################################################################################################################
# Connection/Auth
########################################################################################################################

# API URL.
BASE_URL = ''
if os.environ.get('RUN_ENV') == 'test':
    BASE_URL = "https://testnet.bitmex.com/api/v1/"
else:
    BASE_URL = "https://www.bitmex.com/api/v1/"

# The BitMEX API requires permanent API keys. Go to https://testnet.bitmex.com/api/apiKeys to fill these out.
BITMEX_API_KEY = os.environ.get('BITMEX_API_KEY')
BITMEX_API_SECRET = os.environ.get('BITMEX_API_SECRET')

# Available levels: logging.(DEBUG|INFO|WARN|ERROR)
if os.environ.get('RUN_ENV') == 'test':
    LOG_LEVEL = logging.DEBUG
else:
    LOG_LEVEL = logging.INFO
