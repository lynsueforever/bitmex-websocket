'''BitMEX API Connector.'''
from __future__ import absolute_import
import requests
from time import sleep
import json
import base64
import uuid
import logging
from bitmex_websocket.auth import AccessTokenAuth, APIKeyAuthWithExpires
from bitmex_websocket.utils import constants, errors, log
from bitmex_websocket.websocket import BitMEXWebsocket


class SymbolData(object):

    '''
    An in memory data store of instrument data with convenience accessors.
    '''

    def __init__(
            self,
            symbol='XBTH17',
            otpToken=None,
            apiKey=None,
            apiSecret=None,
            orderIDPrefix='order',
            shouldAuth=False):
        '''Init connector.'''
        self.logger = log.setup_custom_logger(symbol)
        self.symbol = symbol
        self.token = None
        self.apiKey = apiKey
        self.apiSecret = apiSecret

        if len(orderIDPrefix) > 13:
            raise ValueError(
                'settings.ORDERID_PREFIX must be at most 13 characters long!')

        self.orderIDPrefix = orderIDPrefix

        # Prepare HTTPS session
        self.session = requests.Session()
        # These headers are always sent
        self.session.headers.update({
            'user-agent': 'bitmex-websocket-' + constants.VERSION,
            'content-type': 'application/json',
            'accept': 'application/json'
        })

        self.connect_websocket(symbol=symbol)

    #
    # Public methods
    #
    def connect_websocket(self):
        # Create websocket for streaming data
        self.ws = BitMEXWebsocket()
        self.ws.connect(symbol=self.symbol, shouldAuth=self.shouldAuth)

    def ticker_data(self, symbol):
        '''Get ticker data.'''
        '''Return a ticker object. Generated from instrument.'''

        instrument = self.instrument(symbol)

        # If this is an index, we have to get the data from the last trade.
        if instrument['symbol'][0] == '.':
            ticker = {}
            ticker['mid'] = ticker['buy'] = ticker['sell'] = ticker['last'] = instrument['markPrice']
        # Normal instrument
        else:
            bid = instrument['bidPrice'] or instrument['lastPrice']
            ask = instrument['askPrice'] or instrument['lastPrice']
            ticker = {
                "last": instrument['lastPrice'],
                "buy": bid,
                "sell": ask,
                "mid": (bid + ask) / 2
            }

        # The instrument has a tickSize. Use it to round values.
        return {k: round(float(v or 0), instrument['tickLog']) for k, v in iteritems(ticker)}

    def instrument(self, symbol):
        '''Get an instrument's details.'''
        instruments = self.data['instrument']
        matchingInstruments = [i for i in instruments if i['symbol'] == symbol]
        if len(matchingInstruments) == 0:
            raise Exception("Unable to find instrument or index with symbol: " + symbol)
        instrument = matchingInstruments[0]
        # Turn the 'tickSize' into 'tickLog' for use in rounding
        # http://stackoverflow.com/a/6190291/832202
        instrument['tickLog'] = decimal.Decimal(str(instrument['tickSize'])).as_tuple().exponent * -1
        return instrument

    def market_depth(self, symbol):
        '''Get market depth / orderbook.'''
        raise NotImplementedError('orderBook is not subscribed; use askPrice and bidPrice on instrument')
        # return self.data['orderBook25'][0]



    def recent_trades(self):
        '''Get recent trades.

        Returns
        -------
        A list of dicts:
              {u'amount': 60,
               u'date': 1306775375,
               u'price': 8.7401099999999996,
               u'tid': u'93842'},

        '''
        return self.data['trade']

    #
    # Authentication required methods
    #
    def authentication_required(function):
        '''Annotation for methods that require auth.'''
        def wrapped(self, *args, **kwargs):
            if not (self.apiKey):
                msg = "You must be authenticated to use this method"
                raise errors.AuthenticationError(msg)
            else:
                return function(self, *args, **kwargs)
        return wrapped

    @authentication_required
    def funds(self):
        '''Get your current balance.'''
        return self.data['margin'][0]

    @authentication_required
    def position(self, symbol):
        '''Get your open position.'''
        positions = self.data['position']
        pos = [p for p in positions if p['symbol'] == symbol]
        if len(pos) == 0:
            # No position found; stub it
            return {'avgCostPrice': 0, 'avgEntryPrice': 0, 'currentQty': 0, 'symbol': symbol}
        return pos[0]

    @authentication_required
    def buy(self, quantity, price):
        '''Place a buy order.

        Returns order object. ID: orderID
        '''
        return self.place_order(quantity, price)

    @authentication_required
    def sell(self, quantity, price):
        '''Place a sell order.

        Returns order object. ID: orderID
        '''
        return self.place_order(-quantity, price)

    @authentication_required
    def place_order(self, quantity, price):
        '''Place an order.'''
        if price < 0:
            raise Exception("Price must be positive.")

        endpoint = "order"
        # Generate a unique clOrdID with our prefix so we can identify it.
        clOrdID = self.orderIDPrefix + base64.b64encode(uuid.uuid4().bytes).decode('utf-8').rstrip('=\n')
        postdict = {
            'symbol': self.symbol,
            'orderQty': quantity,
            'price': price,
            'clOrdID': clOrdID
        }
        return self._curl_bitmex(api=endpoint, postdict=postdict, verb="POST")

    @authentication_required
    def amend_bulk_orders(self, orders):
        '''Amend multiple orders.'''
        return self._curl_bitmex(api='order/bulk', postdict={'orders': orders}, verb='PUT', rethrow_errors=True)

    @authentication_required
    def create_bulk_orders(self, orders):
        '''Create multiple orders.'''
        for order in orders:
            order['clOrdID'] = self.orderIDPrefix + base64.b64encode(uuid.uuid4().bytes).decode('utf-8').rstrip('=\n')
            order['symbol'] = self.symbol
        return self._curl_bitmex(api='order/bulk', postdict={'orders': orders}, verb='POST')

    @authentication_required
    def open_orders(self):
        '''Get open orders.'''
        orders = self.data['order']
        # Filter to only open orders (leavesQty > 0) and those that we actually placed
        return [o for o in orders if str(o['clOrdID']).startswith(self.orderIDPrefix) and o['leavesQty'] > 0]



    @authentication_required
    def http_open_orders(self):
        '''Get open orders via HTTP. Used on close to ensure we catch them all.'''
        api = "order"
        orders = self._curl_bitmex(
            api=api,
            query={'filter': json.dumps({'ordStatus.isTerminated': False, 'symbol': self.symbol})},
            verb="GET"
        )
        # Only return orders that start with our clOrdID prefix.
        return [o for o in orders if str(o['clOrdID']).startswith(self.orderIDPrefix)]

    @authentication_required
    def cancel(self, orderID):
        '''Cancel an existing order.'''
        api = "order"
        postdict = {
            'orderID': orderID,
        }
        return self._curl_bitmex(api=api, postdict=postdict, verb="DELETE")

    @authentication_required
    def withdraw(self, amount, fee, address):
        api = "user/requestWithdrawal"
        postdict = {
            'amount': amount,
            'fee': fee,
            'currency': 'XBt',
            'address': address
        }
        return self._curl_bitmex(api=api, postdict=postdict, verb="POST")

    def _curl_bitmex(self, api, query=None, postdict=None, timeout=3, verb=None, rethrow_errors=False):
        '''Send a request to BitMEX Servers.'''
        # Handle URL
        url = self.base_url + api

        # Default to POST if data is attached, GET otherwise
        if not verb:
            verb = 'POST' if postdict else 'GET'

        # Auth: Use Access Token by default, API Key/Secret if provided
        auth = AccessTokenAuth(self.token)
        if self.apiKey:
            auth = APIKeyAuthWithExpires(self.apiKey, self.apiSecret)

        def maybe_exit(e):
            if rethrow_errors:
                raise e
            else:
                exit(1)

        # Make the request
        try:
            req = requests.Request(verb, url, json=postdict, auth=auth, params=query)
            prepped = self.session.prepare_request(req)
            response = self.session.send(prepped, timeout=timeout)
            # Make non-200s throw
            response.raise_for_status()

        except requests.exceptions.HTTPError as e:
            # 401 - Auth error. This is fatal with API keys.
            if response.status_code == 401:
                self.logger.error("Login information or API Key incorrect, please check and restart.")
                self.logger.error("Error: " + response.text)
                if postdict:
                    self.logger.error(postdict)
                # Always exit, even if rethrow_errors, because this is fatal
                exit(1)
                return self._curl_bitmex(api, query, postdict, timeout, verb)

            # 404, can be thrown if order canceled does not exist.
            elif response.status_code == 404:
                if verb == 'DELETE':
                    self.logger.error("Order not found: %s" % postdict['orderID'])
                    return
                self.logger.error("Unable to contact the BitMEX API (404). " +
                                  "Request: %s \n %s" % (url, json.dumps(postdict)))
                maybe_exit(e)

            # 429, ratelimit
            elif response.status_code == 429:
                self.logger.error("Ratelimited on current request. Sleeping, then trying again. Try fewer " +
                                  "order pairs or contact support@bitmex.com to raise your limits. " +
                                  "Request: %s \n %s" % (url, json.dumps(postdict)))
                sleep(1)
                return self._curl_bitmex(api, query, postdict, timeout, verb)

            # 503 - BitMEX temporary downtime, likely due to a deploy. Try again
            elif response.status_code == 503:
                self.logger.warning("Unable to contact the BitMEX API (503), retrying. " +
                                    "Request: %s \n %s" % (url, json.dumps(postdict)))
                sleep(1)
                return self._curl_bitmex(api, query, postdict, timeout, verb)

            # Duplicate clOrdID: that's fine, probably a deploy, go get the order and return it
            elif (response.status_code == 400 and
                  response.json()['error'] and
                  response.json()['error']['message'] == 'Duplicate clOrdID'):

                order = self._curl_bitmex('/order',
                                          query={'filter': json.dumps({'clOrdID': postdict['clOrdID']})},
                                          verb='GET')[0]
                if (
                        order['orderQty'] != postdict['quantity'] or
                        order['price'] != postdict['price'] or
                        order['symbol'] != postdict['symbol']):
                    raise Exception('Attempted to recover from duplicate clOrdID, but order returned from API ' +
                                    'did not match POST.\nPOST data: %s\nReturned order: %s' % (
                                        json.dumps(postdict), json.dumps(order)))
                # All good
                return order

            # Unknown Error
            else:
                self.logger.error("Unhandled Error: %s: %s" % (e, response.text))
                self.logger.error("Endpoint was: %s %s: %s" % (verb, api, json.dumps(postdict)))
                maybe_exit(e)

        except requests.exceptions.Timeout as e:
            # Timeout, re-run this request
            self.logger.warning("Timed out, retrying...")
            return self._curl_bitmex(api, query, postdict, timeout, verb)

        except requests.exceptions.ConnectionError as e:
            self.logger.warning("Unable to contact the BitMEX API (ConnectionError). Please check the URL. Retrying. " +
                                "Request: %s \n %s" % (url, json.dumps(postdict)))
            sleep(1)
            return self._curl_bitmex(api, query, postdict, timeout, verb)

        return response.json()