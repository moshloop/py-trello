from datetime import datetime
import json
from dateutil import parser as dateparser
import requests
from requests_oauthlib import OAuth1


class ResourceUnavailable(Exception):
    """Exception representing a failed request to a resource"""

    def __init__(self, msg, http_response):
        Exception.__init__(self)
        self._msg = msg
        self._status = http_response.status_code

    def __str__(self):
        return "%s (HTTP status: %s)" % (
        self._msg, self._status)


class Unauthorized(ResourceUnavailable):
    pass


class TokenError(Exception):
    pass


class TrelloClient(object):
    """ Base class for Trello API access """

    def __init__(self, api_key, api_secret=None, token=None, token_secret=None):
        """
        Constructor

        :api_key: API key generated at https://trello.com/1/appKey/generate
        :api_secret: the secret component of api_key
        :token_key: OAuth token generated by the user in
                    trello.util.create_oauth_token
        :token_secret: the OAuth client secret for the given OAuth token
        """

        # client key and secret for oauth1 session
        if api_key or token:
            self.oauth = OAuth1(client_key=api_key, client_secret=api_secret,
                                resource_owner_key=token, resource_owner_secret=token_secret)
        else:
            self.oauth = None

        self.public_only = token is None
        self.api_key = api_key
        self.api_secret = api_secret
        self.resource_owner_key = token
        self.resource_owner_secret = token_secret

    def info_for_all_boards(self, actions):
        """
        Use this if you want to retrieve info for all your boards in one swoop
        """
        if self.public_only:
            return None
        else:
            json_obj = self.fetch_json(
                '/members/me/boards/all',
                query_params={'actions': actions})
            self.all_info = json_obj

    def logout(self):
        """Log out of Trello."""
        #TODO: This function.

        raise NotImplementedError()

    def list_boards(self):
        """
        Returns all boards for your Trello user

        :return: a list of Python objects representing the Trello boards.
        Each board has the following noteworthy attributes:
            - id: the board's identifier
            - name: Name of the board
            - desc: Description of the board (optional - may be missing from the
                    returned JSON)
            - closed: Boolean representing whether this board is closed or not
            - url: URL to the board
        """
        json_obj = self.fetch_json('/members/me/boards')
        return [Board.from_json(self, json_obj=obj) for obj in json_obj]

    def list_organizations(self):
        """
        Returns all organizations for your Trello user

        :return: a list of Python objects representing the Trello organizations.
        Each organization has the following noteworthy attributes:
            - id: the organization's identifier
            - name: Name of the organization
            - desc: Description of the organization (optional - may be missing from the
                    returned JSON)
            - closed: Boolean representing whether this organization is closed or not
            - url: URL to the organization
        """
        json_obj = self.fetch_json('members/me/organizations')
        return [Organization.from_json(self, obj) for obj in json_obj]

    def get_organization(self, organization_id):
        obj = self.fetch_json('/organizations/' + organization_id)

        return Organization.from_json(self, obj)

    def get_board(self, board_id):
        obj = self.fetch_json('/boards/' + board_id)
        return Board.from_json(self, json_obj=obj)

    def add_board(self, board_name):
        obj = self.fetch_json('/boards', http_method='POST',
                              post_args={'name': board_name})
        return Board.from_json(self, json_obj=obj)

    def get_member(self, member_id):
        return Member(self, member_id).fetch()

    def get_card(self, card_id):
        card_json = self.fetch_json('/cards/' + card_id)
        list_json = self.fetch_json('/lists/' + card_json['idList'])
        board = self.get_board(card_json['idBoard'])
        return Card.from_json(List.from_json(board, list_json), card_json)

    def fetch_json(
            self,
            uri_path,
            http_method='GET',
            headers=None,
            query_params=None,
            post_args=None,
            files=None):
        """ Fetch some JSON from Trello """

        # explicit values here to avoid mutable default values
        if headers is None:
            headers = {}
        if query_params is None:
            query_params = {}
        if post_args is None:
            post_args = {}

        # if files specified, we don't want any data
        data = None
        if files is None:
            data = json.dumps(post_args)

        # set content type and accept headers to handle JSON
        if http_method in ("POST", "PUT", "DELETE") and not files:
            headers['Content-Type'] = 'application/json; charset=utf-8'

        headers['Accept'] = 'application/json'

        # construct the full URL without query parameters
        if uri_path[0] == '/':
            uri_path = uri_path[1:]
        url = 'https://api.trello.com/1/%s' % uri_path

        # perform the HTTP requests, if possible uses OAuth authentication
        response = requests.request(http_method, url, params=query_params,
                                    headers=headers, data=data,
                                    auth=self.oauth, files=files)

        if response.status_code == 401:
            raise Unauthorized("%s at %s" % (response.text, url), response)
        if response.status_code != 200:
            raise ResourceUnavailable("%s at %s" % (response.text, url), response)

        return response.json()

    def list_hooks(self, token=None):
        """
        Returns a list of all hooks associated with a specific token. If you don't pass in a token,
        it tries to use the token associated with the TrelloClient object (if it exists)
        """
        token = token or self.resource_owner_key

        if token is None:
            raise TokenError("You need to pass an auth token in to list hooks.")
        else:
            url = "/tokens/%s/webhooks" % token
            return self._existing_hook_objs(self.fetch_json(url), token)

    def _existing_hook_objs(self, hooks, token):
        """
        Given a list of hook dicts passed from list_hooks, creates
        the hook objects
        """
        all_hooks = []
        for hook in hooks:
            new_hook = WebHook(self, token, hook['id'], hook['description'],
                               hook['idModel'],
                               hook['callbackURL'], hook['active'])
            all_hooks.append(new_hook)
        return all_hooks

    def create_hook(self, callback_url, id_model, desc=None, token=None):
        """
        Creates a new webhook. Returns the WebHook object created.

        There seems to be some sort of bug that makes you unable to create a
        hook using httplib2, so I'm using urllib2 for that instead.
        """
        token = token or self.resource_owner_key

        if token is None:
            raise TokenError("You need to pass an auth token in to create a hook.")

        url = "https://trello.com/1/tokens/%s/webhooks/" % token
        data = {'callbackURL': callback_url, 'idModel': id_model,
                'description': desc}

        response = requests.post(url, data=data, auth=self.oauth)

        if response.status_code == 200:
            hook_id = response.json()['id']
            return WebHook(self, token, hook_id, desc, id_model, callback_url, True)
        else:
            return False

class Organization(object):

    """
    Class representing an organization
    """
    def __init__(self, client, organization_id,   name=''):
        self.client = client
        self.id = organization_id
        self.name = name

    @classmethod
    def from_json(cls, trello_client, json_obj):
        """
        Deserialize the board json object to a Organization object

        :trello_client: the trello client
        :json_obj: the board json object
        """
        organization = Organization(trello_client, json_obj['id'], name=json_obj['name'].encode('utf-8'))
        organization.description = json_obj.get('desc', '').encode('utf-8')
        # cannot close an organization
        #organization.closed = json_obj['closed']
        organization.url = json_obj['url']
        return organization

    def __repr__(self):
        return '<Organization %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this organization"""
        json_obj = self.client.fetch_json('/organizations/' + self.id)
        self.name = json_obj['name']
        self.description = json_obj.get('desc', '')
        self.closed = json_obj['closed']
        self.url = json_obj['url']

    def all_boards(self):
        """Returns all boards on this organization"""
        return self.get_boards('all')

    def get_boards(self, list_filter):
        # error checking
        json_obj = self.client.fetch_json(
            '/organizations/' + self.id + '/boards',
            query_params={'lists': 'none', 'filter': list_filter})
        return [Board.from_json(organization=self, json_obj=obj) for obj in json_obj]

    def get_board(self, field_name):
        # error checking
        json_obj = self.client.fetch_json(
            '/organizations/' + self.id + '/boards',
            query_params={'filter': 'open','fields':field_name})
        return [Board.from_json(organization=self, json_obj=obj) for obj in json_obj]

    def get_members(self):
        json_obj = self.client.fetch_json(
        '/organizations/' + self.id + '/members',
        query_params={'filter': 'all'})
        return [Member.from_json(trello_client=self.client, json_obj=obj) for obj in json_obj]

class Board(object):
    """
    Class representing a Trello board. Board attributes are stored as normal
    Python attributes; access to all sub-objects, however, is always
    an API call (Lists, Cards).
    """

    def __init__(self, client=None, board_id=None, organization=None, name=''):
        """
        :trello: Reference to a Trello object
        :board_id: ID for the board

        Alternative Constructor

        :organization: reference to the parent organization
        :board_id: ID for this board

        """
        if organization is None:
            self.client = client
        else:
            self.organization = organization
            self.client = organization.client
        self.id = board_id
        self.name = name


    @classmethod
    def from_json(cls, trello_client=None, organization = None, json_obj=None):
        """
        Deserialize the board json object to a Board object

        :trello_client: the trello client
        :json_obj: the board json object

        Alternative contrustraction:

        Deserialize the board json object to a board object

        :organization: the organization object that the board belongs to
        :json_obj: the json board object
        """
        if organization is None:
            board = Board(client=trello_client, board_id=json_obj['id'], name=json_obj['name'].encode('utf-8'))
        else:
            board = Board(organization=organization, board_id=json_obj['id'], name=json_obj['name'].encode('utf-8'))

        board.description = json_obj.get('desc', '').encode('utf-8')
        board.closed = json_obj['closed']
        board.url = json_obj['url']
        return board

    def __repr__(self):
        return '<Board %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this board"""
        json_obj = self.client.fetch_json('/boards/' + self.id)
        self.name = json_obj['name']
        self.description = json_obj.get('desc', '')
        self.closed = json_obj['closed']
        self.url = json_obj['url']

    def save(self):
        pass

    def close(self):
        self.client.fetch_json(
            '/boards/' + self.id + '/closed',
            http_method='PUT',
            post_args={'value': 'true', }, )
        self.closed = True

    def open(self):
        self.client.fetch_json(
            '/boards/' + self.id + '/closed',
            http_method='PUT',
            post_args={'value': 'false', }, )
        self.closed = False

    def get_list(self, list_id):
        obj = self.client.fetch_json('/lists/' + list_id)
        return List.from_json(board=self, json_obj=obj)

    def all_lists(self):
        """Returns all lists on this board"""
        return self.get_lists('all')

    def open_lists(self):
        """Returns all open lists on this board"""
        return self.get_lists('open')

    def closed_lists(self):
        """Returns all closed lists on this board"""
        return self.get_lists('closed')

    def get_lists(self, list_filter):
        # error checking
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/lists',
            query_params={'cards': 'none', 'filter': list_filter})
        return [List.from_json(board=self, json_obj=obj) for obj in json_obj]

    def add_list(self, name):
        """Add a list to this board

        :name: name for the list
        :return: the list
        """
        obj = self.client.fetch_json(
            '/lists',
            http_method='POST',
            post_args={'name': name, 'idBoard': self.id}, )
        return List.from_json(board=self, json_obj=obj)

    def all_cards(self):
        """Returns all cards on this board"""
        filters = {
            'filter': 'all',
            'fields': 'all'
        }
        return self.get_cards(filters)

    def open_cards(self):
        """Returns all open cards on this board"""
        filters = {
            'filter': 'open',
            'fields': 'all'
        }
        return self.get_cards(filters)

    def closed_cards(self):
        """Returns all closed cards on this board"""
        filters = {
            'filter': 'closed',
            'fields': 'all'
        }
        return self.get_cards(filters)

    def get_cards(self, filters=None):
        """
        :card_filter: filters on card status ('open', 'closed', 'all')
        :query_params: dict containing query parameters. Eg. {'fields': 'all'}

        More info on card queries:
        https://trello.com/docs/api/board/index.html#get-1-boards-board-id-cards
        """
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/cards',
            query_params=filters
        )

        return list([Card.from_json(self, json) for json in json_obj])

    def all_members(self):
        """Returns all members on this board"""
        filters = {
            'filter': 'all',
            'fields': 'all'
        }
        return self.get_members(filters)

    def normal_members(self):
        """Returns all normal members on this board"""
        filters = {
            'filter': 'normal',
            'fields': 'all'
        }
        return self.get_members(filters)

    def admin_members(self):
        """Returns all admin members on this board"""
        filters = {
            'filter': 'admins',
            'fields': 'all'
        }
        return self.get_members(filters)

    def owner_members(self):
        """Returns all owner members on this board"""
        filters = {
            'filter': 'owners',
            'fields': 'all'
        }
        return self.get_members(filters)

    def get_members(self, filters=None):
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/members',
            query_params=filters)
        members = list()
        for obj in json_obj:
            m = Member(self.client, obj['id'])
            m.status = obj['status'].encode('utf-8')
            m.id = obj.get('id', '')
            m.bio = obj.get('bio', '')
            m.url = obj.get('url', '')
            m.username = obj['username'].encode('utf-8')
            m.full_name = obj['fullName'].encode('utf-8')
            m.initials = obj['initials'].encode('utf-8')
            members.append(m)

        return members

    def fetch_actions(self, action_filter):
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/actions',
            query_params={'filter': action_filter})
        self.actions = json_obj


class List(object):
    """
    Class representing a Trello list. List attributes are stored on the object,
    but access to sub-objects (Cards) require an API call
    """

    def __init__(self, board, list_id, name=''):
        """Constructor

        :board: reference to the parent board
        :list_id: ID for this list
        """
        self.board = board
        self.client = board.client
        self.id = list_id
        self.name = name

    @classmethod
    def from_json(cls, board, json_obj):
        """
        Deserialize the list json object to a List object

        :board: the board object that the list belongs to
        :json_obj: the json list object
        """
        list = List(board, json_obj['id'], name=json_obj['name'].encode('utf-8'))
        list.closed = json_obj['closed']
        return list

    def __repr__(self):
        return '<List %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this list"""
        json_obj = self.client.fetch_json('/lists/' + self.id)
        self.name = json_obj['name']
        self.closed = json_obj['closed']

    def list_cards(self):
        """Lists all cards in this list"""
        json_obj = self.client.fetch_json('/lists/' + self.id + '/cards')
        return [Card.from_json(self, c) for c in json_obj]

    def add_card(self, name, desc=None):
        """Add a card to this list

        :name: name for the card
        :return: the card
        """
        json_obj = self.client.fetch_json(
            '/lists/' + self.id + '/cards',
            http_method='POST',
            post_args={'name': name, 'idList': self.id, 'desc': desc}, )
        return Card.from_json(self, json_obj)

    def fetch_actions(self, action_filter):
        """
        Fetch actions for this list can give more argv to action_filter,
        split for ',' json_obj is list
        """
        json_obj = self.client.fetch_json(
            '/lists/' + self.id + '/actions',
            query_params={'filter': action_filter})
        self.actions = json_obj

    def _set_remote_attribute(self, attribute, value):
        self.client.fetch_json(
            '/lists/' + self.id + '/' + attribute,
            http_method='PUT',
            post_args={'value': value, }, )

    def close(self):
        self.client.fetch_json(
            '/lists/' + self.id + '/closed',
            http_method='PUT',
            post_args={'value': 'true', }, )
        self.closed = True

    def open(self):
        self.client.fetch_json(
            '/lists/' + self.id + '/closed',
            http_method='PUT',
            post_args={'value': 'false', }, )
        self.closed = False

    def cardsCnt(self):
        return len(self.list_cards())

class Card(object):
    """
    Class representing a Trello card. Card attributes are stored on
    the object
    """

    @property
    def member_id(self):
        return self.idMembers

    @property
    def short_id(self):
        return self.idShort

    @property
    def list_id(self):
        return self.idList

    @property
    def board_id(self):
        return self.idBoard

    @property
    def description(self):
        return self.desc

    @property
    def date_last_activity(self):
        return self.dateLastActivity

    @description.setter
    def description(self, value):
        self.desc = value

    @property
    def idLabels(self):
        return self.label_ids

    @idLabels.setter
    def idLabels(self, values):
        self.label_ids = values

    @property
    def list_labels(self):
        if self.labels:
            return self.labels
        return None

    @property
    def comments(self):
        """
        Lazily loads and returns the comments
        """
        if self._comments is None:
            self._comments = self.fetch_comments()
        return self._comments

    @property
    def checklists(self):
        """
        Lazily loads and returns the checklists
        """
        if self._checklists is None:
            self._checklists = self.fetch_checklists()
        return self._checklists

    def __init__(self, trello_list, card_id, name=''):
        """
        :trello_list: reference to the parent list
        :card_id: ID for this card
        """
        self.trello_list = trello_list
        self.client = trello_list.client
        self.id = card_id
        self.name = name

    @classmethod
    def from_json(cls, trello_list, json_obj):
        """
        Deserialize the card json object to a Card object

        :trello_list: the list object that the card belongs to
        :json_obj: json object
        """
        if 'id' not in json_obj:
            raise Exception("key 'id' is not in json_obj")
        card = cls(trello_list,
                   json_obj['id'],
                   name=json_obj['name'].encode('utf-8'))
        card.desc = json_obj.get('desc', '')
        card.closed = json_obj['closed']
        card.url = json_obj['url']
        card.member_ids = json_obj['idMembers']
        card.idLabels = json_obj['idLabels']
        card.labels = json_obj['labels']
        return card

    def __repr__(self):
        return '<Card %s>' % self.name

    def fetch(self, eager=True):
        """
        Fetch all attributes for this card
        :param eager: If eager is true comments and checklists will be fetched immediately, otherwise on demand
        """
        json_obj = self.client.fetch_json(
            '/cards/' + self.id,
            query_params={'badges': False})
        self.id = json_obj['id']
        self.name = json_obj['name'].encode('utf-8')
        self.desc = json_obj.get('desc', '')
        self.closed = json_obj['closed']
        self.url = json_obj['url']
        self.idMembers = json_obj['idMembers']
        self.idShort = json_obj['idShort']
        self.idList = json_obj['idList']
        self.idBoard = json_obj['idBoard']
        self.idLabels = json_obj['idLabels']
        self.labels = json_obj['labels']
        self.badges = json_obj['badges']
        # For consistency, due date is in YYYY-MM-DD format
        if json_obj.get('due', ''):
            self.due = json_obj.get('due', '')[:10]
        self.checked = json_obj['checkItemStates']
        self.dateLastActivity = dateparser.parse(json_obj['dateLastActivity'])

        self._checklists = self.fetch_checklists() if eager else None
        self._comments = self.fetch_comments() if eager else None

    def fetch_comments(self):
        comments = []

        if self.badges['comments'] > 0:
            comments = self.client.fetch_json(
                '/cards/' + self.id + '/actions',
                query_params={'filter': 'commentCard'})

        return comments

    def get_comments(self):
        comments = []
        comments = self.client.fetch_json(
                '/cards/' + self.id + '/actions',
                query_params={'filter': 'commentCard'})
        return comments
    
    def fetch_checklists(self):
        checklists = []
        json_obj = self.client.fetch_json(
            '/cards/' + self.id + '/checklists', )
        for cl in json_obj:
            checklists.append(Checklist(self.client, self.checked, cl,
                                        trello_card=self.id))
        return checklists

    def fetch_actions(self, action_filter='createCard'):
        """
        Fetch actions for this card can give more argv to action_filter,
        split for ',' json_obj is list
        """
        json_obj = self.client.fetch_json(
            '/cards/' + self.id + '/actions',
            query_params={'filter': action_filter})
        self.actions = json_obj


    def attriExp(self, multiple):
        """
            Provides the option to explore what comes from trello
            :multiple is one of the attributes of GET /1/cards/[card id or shortlink]/actions
        """
        self.fetch_actions(multiple)
        return self.actions

    def listCardMove_date(self):
        """
            Will return the history of transitions of a card from one list to another
            The lower the index the more resent the historical item

            It returns a list of lists. The sublists are triplates of
            starting list, ending list and when the transition occured.
        """
        self.fetch_actions('updateCard:idList')
        res =[]
        for idx in self.actions:
            date_str = idx['date'][:-5]
            dateDate = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
            strLst = idx['data']['listBefore']['name']
            endLst = idx['data']['listAfter']['name']
            res.append([strLst,endLst,dateDate])
        return res

    @property
    def latestCardMove_date(self):
        """
            returns the date of the last card transition

        """
        self.fetch_actions('updateCard:idList')
        date_str = self.actions[0]['date'][:-5]
        return datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')

    @property
    def create_date(self):
        """
            Will return the creation date of the card.
            WARNING: if the card was create via convertion of a checklist item
                    it fails. attriExp('convertToCardFromCheckItem') allows to
                    test for the condition.
        """
        self.fetch_actions()
        date_str = self.actions[0]['date'][:-5]
        return datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')

    def set_name(self, new_name):
        """
        Update the name on the card to :new_name:
        """
        self._set_remote_attribute('name', new_name)
        self.name = new_name

    def set_description(self, description):
        self._set_remote_attribute('desc', description)
        self.desc = description

    def set_due(self, due):
        """Set the due time for the card

        :title: due a datetime object
        """
        datestr = due.strftime('%Y-%m-%d')
        self._set_remote_attribute('due', datestr)
        self.due = datestr

    def set_closed(self, closed):
        self._set_remote_attribute('closed', closed)
        self.closed = closed

    def delete(self):
        # Delete this card permanently
        self.client.fetch_json(
            '/cards/' + self.id,
            http_method='DELETE', )

    def assign(self, member_id):
        self.client.fetch_json(
            '/cards/' + self.id + '/members',
            http_method='POST',
            post_args={'value': member_id, })

    def comment(self, comment_text):
        """Add a comment to a card."""
        self.client.fetch_json(
            '/cards/' + self.id + '/actions/comments',
            http_method='POST',
            post_args={'text': comment_text, })

    def attach(self, name=None, mimeType=None, file=None, url=None):
        """
        Add an attachment to the card. The attachment can be either a
        file or a url. Setting the name and/or mime type is optional.
        :param name: The name of the attachment
        :param mimeType: mime type for the attachement
        :param file: a file-like, binary object that supports read()
        :param url: a URL pointing to the resource to be attached
        """
        if (file and url) or (not file and not url):
            raise Exception('Please provide either a file or url, and not both!')

        kwargs = {}
        if file:
            kwargs['files'] = dict(file=(name, file, mimeType))
        else:
            kwargs['name'] = name
            kwargs['mimeType'] = mimeType
            kwargs['url'] = url

        self._post_remote_data(
            'attachments', **kwargs
        )

    def change_list(self, list_id):
        self.client.fetch_json(
            '/cards/' + self.id + '/idList',
            http_method='PUT',
            post_args={'value': list_id, })

    def change_board(self, board_id, list_id=None):
        args = {'value': board_id, }
        if list_id is not None:
            args['idList'] = list_id
        self.client.fetch_json(
            '/cards/' + self.id + '/idBoard',
            http_method='PUT',
            post_args=args)

    def add_checklist(self, title, items, itemstates=None):

        """Add a checklist to this card

        :title: title of the checklist
        :items: a list of the item names
        :itemstates: a list of the state (True/False) of each item
        :return: the checklist
        """
        if itemstates is None:
            itemstates = []

        json_obj = self.client.fetch_json(
            '/cards/' + self.id + '/checklists',
            http_method='POST',
            post_args={'name': title}, )

        cl = Checklist(self.client, [], json_obj, trello_card=self.id)
        for i, name in enumerate(items):
            try:
                checked = itemstates[i]
            except IndexError:
                checked = False
            cl.add_checklist_item(name, checked)

        self.fetch()
        return cl

    def _set_remote_attribute(self, attribute, value):
        self.client.fetch_json(
            '/cards/' + self.id + '/' + attribute,
            http_method='PUT',
            post_args={'value': value, }, )

    def _post_remote_data(self, attribute, files=None, **kwargs):
        self.client.fetch_json(
            '/cards/' + self.id + '/' + attribute,
            http_method='POST',
            files=files,
            post_args=kwargs )

class Label(object):
    """
    Class representing a Trello Label.
    """
    def __init__(self, client, label_id, name, color=""):
        self.client = client
        self.id = label_id
        self.name = name
        self.color = color

    def __repr__(self):
        return '<Label %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this label"""
        json_obj = self.client.fetch_json(
            '/labels/' + self.id)
        self.name = json_obj['name'].encode('utf-8')
        self.color = json_obj['color']
        return self

class Member(object):
    """
    Class representing a Trello member.
    """

    def __init__(self, client, member_id, full_name=''):
        self.client = client
        self.id = member_id
        self.full_name = full_name


    def __repr__(self):
        return '<Member %s>' % self.id

    def fetch(self):
        """Fetch all attributes for this card"""
        json_obj = self.client.fetch_json(
            '/members/' + self.id,
            query_params={'badges': False})
        self.status = json_obj['status']
        self.id = json_obj.get('id', '')
        self.bio = json_obj.get('bio', '')
        self.url = json_obj.get('url', '')
        self.username = json_obj['username']
        self.full_name = json_obj['fullName']
        self.initials = json_obj['initials']
        self.commentCard = json_obj['commentCard']
        return self

    def fetch_comments(self):
        comments = []
        if self.badges['comments'] > 0:
            comments = self.client.fetch_json(
                '/members/' + self.id + '/actions',
                query_params={'filter': 'commentCard'})
        return comments

    @classmethod
    def from_json(cls, trello_client, json_obj):
        """
        Deserialize the organization json object to a member object

        :trello_client: the trello client
        :json_obj: the member json object
        """

        member = Member(trello_client, json_obj['id'], full_name=json_obj['fullName'].encode('utf-8'))
        member.username = json_obj.get('username', '').encode('utf-8')
        member.initials = json_obj.get('initials', '').encode('utf-8')
        # cannot close an organization
        #organization.closed = json_obj['closed']
        return member



class Checklist(object):
    """
    Class representing a Trello checklist.
    """

    def __init__(self, client, checked, obj, trello_card=None):
        self.client = client
        self.trello_card = trello_card
        self.id = obj['id']
        self.name = obj['name']
        self.items = obj['checkItems']
        for i in self.items:
            i['checked'] = False
            for cis in checked:
                if cis['idCheckItem'] == i['id'] and cis['state'] == 'complete':
                    i['checked'] = True

    def add_checklist_item(self, name, checked=False):
        """Add a checklist item to this checklist

        :name: name of the checklist item
        :checked: True if item state should be checked, False otherwise
        :return: the checklist item json object
        """
        json_obj = self.client.fetch_json(
            '/checklists/' + self.id + '/checkItems',
            http_method='POST',
            post_args={'name': name, 'checked': checked}, )
        json_obj['checked'] = checked
        self.items.append(json_obj)
        return json_obj

    def set_checklist_item(self, name, checked):
        """Set the state of an item on this checklist

        :name: name of the checklist item
        :checked: True if item state should be checked, False otherwise
        """

        # Locate the id of the checklist item
        try:
            [ix] = [i for i in range(len(self.items)) if
                    self.items[i]['name'] == name]
        except ValueError:
            return

        json_obj = self.client.fetch_json(
            '/cards/' + self.trello_card + \
            '/checklist/' + self.id + \
            '/checkItem/' + self.items[ix]['id'],
            http_method='PUT',
            post_args={'state': 'complete' if checked else 'incomplete'})

        json_obj['checked'] = checked
        self.items[ix] = json_obj
        return json_obj

    def rename(self, new_name):
        """Rename this checklist

        :new_name: new name of the checklist
        """

        json_obj = self.client.fetch_json(
            '/checklists/' + self.id + '/name/',
            http_method='PUT',
            post_args={'value': new_name})

        self.name = json_obj['name']

        return json_obj

    def rename_checklist_item(self, name, new_name):
        """Rename the item on this checklist

        :name: name of the checklist item
        :new_name: new name of item
        """

        # Locate the id of the checklist item
        try:
            [ix] = [i for i in range(len(self.items)) if self.items[i]['name'] == name]
        except ValueError:
            return

        json_obj = self.client.fetch_json(
                '/cards/'+self.trello_card+\
                '/checklist/'+self.id+\
                '/checkItem/'+self.items[ix]['id'],
                http_method = 'PUT',
                post_args = {'name' : new_name})

        self.items[ix] = json_obj
        return json_obj

    def delete(self):
        """Removes this checklist"""
        self.client.fetch_json(
            '/checklists/%s' % self.id,
            http_method='DELETE')

    def __repr__(self):
        return '<Checklist %s>' % self.id


class WebHook(object):
    """Class representing a Trello webhook."""

    def __init__(self, client, token, hook_id=None, desc=None, id_model=None,
                 callback_url=None, active=False):
        self.id = hook_id
        self.desc = desc
        self.id_model = id_model
        self.callback_url = callback_url
        self.active = active
        self.client = client
        self.token = token

    def delete(self):
        """Removes this webhook from Trello"""
        self.client.fetch_json(
            '/webhooks/%s' % self.id,
            http_method='DELETE')

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
