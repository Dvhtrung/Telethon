import asyncio
import datetime
from pathlib import Path
from types import TracebackType
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Self,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from ...mtsender import Sender
from ...session import (
    ChatHashCache,
    MemorySession,
    MessageBox,
    PackedChat,
    Session,
    SqliteSession,
    Storage,
)
from ...tl import Request, abcs
from ..events import Event
from ..events.filters import Filter
from ..types import (
    AsyncList,
    Chat,
    ChatLike,
    Dialog,
    Draft,
    File,
    InFileLike,
    LoginToken,
    Message,
    OutFileLike,
    Participant,
    PasswordToken,
    RecentAction,
    User,
)
from .auth import (
    bot_sign_in,
    check_password,
    interactive_login,
    is_authorized,
    request_login_code,
    sign_in,
    sign_out,
)
from .bots import InlineResult, inline_query
from .chats import (
    get_admin_log,
    get_participants,
    get_profile_photos,
    set_admin_rights,
    set_banned_rights,
    set_default_rights,
)
from .dialogs import delete_dialog, get_dialogs, get_drafts
from .files import (
    download,
    get_file_bytes,
    send_audio,
    send_file,
    send_photo,
    send_video,
)
from .messages import (
    MessageMap,
    build_message_map,
    delete_messages,
    edit_message,
    forward_messages,
    get_messages,
    get_messages_with_ids,
    pin_message,
    search_all_messages,
    search_messages,
    send_message,
    unpin_message,
)
from .net import (
    DEFAULT_DC,
    Config,
    connect,
    connected,
    disconnect,
    invoke_request,
    run_until_disconnected,
)
from .updates import (
    add_event_handler,
    get_handler_filter,
    on,
    remove_event_handler,
    set_handler_filter,
)
from .users import (
    get_contacts,
    get_me,
    input_to_peer,
    resolve_to_packed,
    resolve_username,
)

Return = TypeVar("Return")
T = TypeVar("T")


class Client:
    """
    A client capable of connecting to Telegram and sending requests.

    This is the "entry point" of the library.

    This class can be used as an asynchronous context manager to automatically :meth:`connect` and :meth:`disconnect`:

    .. code-block:: python

        async with Client(session, api_id, api_hash) as client:
            ...  # automatically connect()-ed

        ...  # after exiting the block, disconnect() was automatically called

    :param session:
        A name or path to a ``.session`` file, or a different storage.

    :param api_id:
        The API ID. See :doc:`/basic/signing-in` to learn how to obtain it.

    :param api_hash:
        The API hash. See :doc:`/basic/signing-in` to learn how to obtain it.

    :param device_model:
        Device model.

    :param system_version:
        System version.

    :param app_version:
        Application version.

    :param system_lang_code:
        ISO 639-1 language code of the system's language.

    :param lang_code:
        ISO 639-1 language code of the application's language.

    :param catch_up:
        Whether to "catch up" on updates that occured while the client was not connected.

    :param server_addr:
        Override the server address ``'ip:port'`` pair to connect to.
        Useful to connect to one of Telegram's test servers.

    :param flood_sleep_threshold:
        Maximum amount of time, in seconds, to automatically sleep before retrying a request.
        This sleeping occurs when ``FLOOD_WAIT`` :class:`~telethon.RpcError` is raised by Telegram.

    :param update_queue_limit:
        Maximum amount of updates to keep in memory before dropping them.

    :param check_all_handlers:
        Whether to always check all event handlers or stop early.

        The library will call event handlers in the order they were added.
        By default, the library stops checking handlers as soon as a filter returns :data:`True`.

        By setting ``check_all_handlers=True``, the library will keep calling handlers after the first match.
    """

    def __init__(
        self,
        session: Optional[Union[str, Path, Storage]],
        api_id: int,
        api_hash: Optional[str] = None,
        check_all_handlers: bool = False,
    ) -> None:
        self._sender: Optional[Sender] = None
        self._sender_lock = asyncio.Lock()
        self._dc_id = DEFAULT_DC
        if isinstance(session, Storage):
            self._storage = session
        elif session is None:
            self._storage = MemorySession()
        else:
            self._storage = SqliteSession(session)
        self._config = Config(
            session=Session(),
            api_id=api_id,
            api_hash=api_hash or "",
        )

        self._message_box = MessageBox()
        self._chat_hashes = ChatHashCache(None)
        self._last_update_limit_warn: Optional[float] = None
        self._updates: asyncio.Queue[
            Tuple[abcs.Update, Dict[int, Chat]]
        ] = asyncio.Queue(maxsize=self._config.update_queue_limit or 0)
        self._dispatcher: Optional[asyncio.Task[None]] = None
        self._handlers: Dict[
            Type[Event], List[Tuple[Callable[[Any], Awaitable[Any]], Optional[Filter]]]
        ] = {}
        self._shortcircuit_handlers = not check_all_handlers

        if self_user := self._config.session.user:
            self._dc_id = self_user.dc
            if self._config.catch_up and self._config.session.state:
                self._message_box.load(self._config.session.state)

    # Begin partially @generated

    def add_event_handler(self, handler: Callable[[Event], Awaitable[Any]], event_cls: Type[Event], filter: Optional[Filter]=None) -> None:
        """
        Register a callable to be invoked when the provided event type occurs.

        :param handler:
            The callable to invoke when an event occurs.
            This is often just a function object.

        :param event_cls:
            The event type to bind to the handler.
            When Telegram sends an update corresponding to this type,
            *handler* is called with an instance of this event type as the only argument.

        :param filter:
            Filter function to call with the event before calling *handler*.
            If it returns `False`, *handler* will not be called.
            See the :mod:`~telethon.events.filters` module to learn more.

        .. rubric:: Example

        .. code-block:: python

            async def my_print_handler(event):
                print(event.chat.full_name, event.text)

            # Register a handler to be called on new messages
            client.add_event_handler(my_print_handler, events.NewMessage)

            # Register a handler to be called on new messages if they contain "hello" or "/start"
            from telethon.events import filters

            client.add_event_handler(
                my_print_handler,
                events.NewMessage,
                filters.Any(filters.Text(r'hello'), filters.Command('/start')),
            )

        .. seealso::

            :meth:`on`, used to register handlers with the decorator syntax.
        """
        add_event_handler(self, handler, event_cls, filter)

    async def bot_sign_in(self, token: str) -> User:
        """
        Sign in to a bot account.

        :param token:
            The bot token obtained from `@BotFather <https://t.me/BotFather>`_.
            It's a string composed of digits, a colon, and characters from the base-64 alphabet.

        :return: The bot user corresponding to :term:`yourself`.

        .. rubric:: Example

        .. code-block:: python

            await client.bot_sign_in('12345:abc67DEF89ghi')

        .. seealso::

            :meth:`request_login_code`, used to sign in as a user instead.
        """
        return await bot_sign_in(self, token)

    async def check_password(self, token: PasswordToken, password: Union[str, bytes]) -> User:
        """
        Check the two-factor-authentication (2FA) password.
        If it is correct, completes the login.

        :param token:
            The return value from :meth:`sign_in`.

        :param password:
            The 2FA password.

        :return: The user corresponding to :term:`yourself`.

        .. rubric:: Example

        .. code-block:: python

            from telethon.types import PasswordToken

            login_token = await client.request_login_code('+1 23 456')
            password_token = await client.sign_in(login_token, input('code: '))
            assert isinstance(password_token, PasswordToken)

            user = await client.check_password(password_token, '1-L0V3+T3l3th0n')

        .. seealso::

            :meth:`request_login_code` and :meth:`sign_in`
        """
        return await check_password(self, token, password)

    async def connect(self) -> None:
        """
        Connect to the Telegram servers.

        .. rubric:: Example

        .. code-block:: python

            await client.connect()
            # success!
        """
        await connect(self)

    async def delete_dialog(self, chat: ChatLike) -> None:
        """
        Delete a dialog.

        This lets you leave a group, unsubscribe from a channel, or delete a one-to-one private conversation.

        Note that the group or channel will not be deleted.

        Note that bot accounts do not have dialogs, so this method will fail.

        :param chat:
            The :term:`chat` representing the dialog to delete.

        .. rubric:: Example

        .. code-block:: python

            async for dialog in client.iter_dialogs():
                if 'dog pictures' in dialog.chat.full_name:
                    # You've realized you're more of a cat person
                    await client.delete_dialog(dialog.chat)
        """
        await delete_dialog(self, chat)

    async def delete_messages(self, chat: ChatLike, message_ids: List[int], *, revoke: bool=True) -> int:
        """
        Delete messages.

        :param chat:
            The :term:`chat` where the messages are.

            .. warning::

                When deleting messages from private conversations or small groups,
                this parameter is ignored. This means the *message_ids* may delete
                messages in different chats.

        :param message_ids:
            The list of message identifiers to delete.

        :param revoke:
            When set to :data:`True`, the message will be deleted for everyone that is part of *chat*.
            Otherwise, the message will only be deleted for :term:`yourself`.

        :return: The amount of messages that were deleted.

        .. rubric:: Example

        .. code-block:: python

            # Delete two messages from chat for yourself
            await client.delete_messages(
                chat,
                [187481, 187482],
                revoke=False,
            )

        .. seealso::

            :meth:`telethon.types.Message.delete`
        """
        return await delete_messages(self, chat, message_ids, revoke=revoke)

    async def disconnect(self) -> None:
        """
        Disconnect from the Telegram servers.

        This call will only fail if saving the :term:`session` fails.

        .. rubric:: Example

        .. code-block:: python

            await client.disconnect()
            # success!
        """
        await disconnect(self)

    async def download(self, media: File, file: Union[str, Path, OutFileLike]) -> None:
        """
        Download a file.

        :param media:
            The media file to download.
            This will often come from :attr:`telethon.types.Message.file`.

        :param file:
            The output file path or :term:`file-like object`.
            Note that the extension is not automatically added to the path.
            You can get the file extension with :attr:`telethon.types.File.ext`.

            .. warning::

                If the file already exists, it will be overwritten!

        .. rubric:: Example

        .. code-block:: python

            if photo := message.photo:
                await client.download(photo, 'picture.jpg')

            if video := message.video:
                with open('video.mp4, 'wb') as file:
                    await client.download(video, file)

        .. seealso::

            :meth:`get_file_bytes`, for more control over the download.
        """
        await download(self, media, file)

    async def edit_message(self, chat: ChatLike, message_id: int, *, text: Optional[str]=None, markdown: Optional[str]=None, html: Optional[str]=None, link_preview: Optional[bool]=None) -> Message:
        """
        Edit a message.

        :param chat:
            The :term:`chat` where the message to edit is.

        :param message_id:
            The identifier of the message to edit.

        The rest of parameters behave the same as they do in `send_message` or `send_file`.

        :return: The edited message.

        .. rubric:: Example

        .. code-block:: python

            # Edit message to have text without formatting
            await client.edit_message(chat, msg_id, text='New text')

            # Remove the link preview without changing the text
            await client.edit_message(chat, msg_id, link_preview=False)

        .. seealso::

            :meth:`telethon.types.Message.edit`
        """
        return await edit_message(self, chat, message_id, text=text, markdown=markdown, html=html, link_preview=link_preview)

    async def forward_messages(self, target: ChatLike, message_ids: List[int], source: ChatLike) -> List[Message]:
        """
        Forward messages from one :term:`chat` to another.

        :param target:
            The :term:`chat` where the messages will be forwarded to.

        :param message_ids:
            The list of message identifiers to forward.

        :param source:
            The source :term:`chat` where the messages to forward exist.

        :return: The forwarded messages.

        .. rubric:: Example

        .. code-block:: python

            # Forward two messages from chat to the destination
            await client.forward_messages(
                destination,
                [187481, 187482],
                chat,
            )

        .. seealso::

            :meth:`telethon.types.Message.forward_to`
        """
        return await forward_messages(self, target, message_ids, source)

    def get_admin_log(self, chat: ChatLike) -> AsyncList[RecentAction]:
        """
        Get the recent actions from the administrator's log.

        This method requires you to be an administrator in the :term:`chat`.

        The returned actions are also known as "admin log events".

        :param chat:
            The :term:`chat` to fetch recent actions from.

        :return: The recent actions.

        .. rubric:: Example

        .. code-block:: python

            async for admin_log_event in client.get_admin_log(chat):
                if message := admin_log_event.deleted_message:
                    print('Deleted:', message.text)
        """
        return get_admin_log(self, chat)

    def get_contacts(self) -> AsyncList[User]:
        """
        Get the users in your contact list.

        :return: Your contacts.

        .. rubric:: Example

        .. code-block:: python

            async for user in client.get_contacts():
                print(user.full_name, user.id)
        """
        return get_contacts(self)

    def get_dialogs(self) -> AsyncList[Dialog]:
        """
        Get the dialogs you're part of.

        This list of includes the groups you've joined, channels you've subscribed to, and open one-to-one private conversations.

        Note that bot accounts do not have dialogs, so this method will fail.

        :return: Your dialogs.

        .. rubric:: Example

        .. code-block:: python

            async for dialog in client.get_dialogs():
                print(
                    dialog.chat.full_name,
                    dialog.last_message.text if dialog.last_message else ''
                )
        """
        return get_dialogs(self)

    def get_drafts(self) -> AsyncList[Draft]:
        """
        Get all message drafts saved in any dialog.

        :return: The existing message drafts.

        .. rubric:: Example

        .. code-block:: python

            async for draft in client.get_drafts():
                await draft.delete()
        """
        return get_drafts(self)

    def get_file_bytes(self, media: File) -> AsyncList[bytes]:
        """
        Get the contents of an uploaded media file as chunks of :class:`bytes`.

        This lets you iterate over the chunks of a file and print progress while the download occurs.

        If you just want to download a file to disk without printing progress, use :meth:`download` instead.

        :param media:
            The media file to download.
            This will often come from :attr:`telethon.types.Message.file`.

        .. rubric:: Example

        .. code-block:: python

            if file := message.file:
                with open(f'media{file.ext}', 'wb') as fd:
                    downloaded = 0
                    async for chunk in client.get_file_bytes(file):
                        downloaded += len(chunk)
                        fd.write(chunk)
                        print(f'Downloaded {downloaded // 1024}/{file.size // 1024} KiB')
        """
        return get_file_bytes(self, media)

    def get_handler_filter(self, handler: Callable[[Event], Awaitable[Any]]) -> Optional[Filter]:
        """
        Get the filter associated to the given event handler.

        :param handler:
            The callable that was previously added as an event handler.

        :return:
            The filter, if *handler* was actually registered and had a filter.

        .. rubric:: Example

        .. code-block:: python

            from telethon.events import filters

            # Get the current filter...
            filt = client.get_handler_filter(my_handler)

            # ...and "append" a new filter that also must match.
            client.set_handler_filter(my_handler, filters.All(filt, filt.Text(r'test')))
        """
        return get_handler_filter(self, handler)

    async def get_me(self) -> Optional[User]:
        """
        Get information about :term:`yourself`.

        :return:
            The user associated with the logged-in account, or :data:`None` if the client is not authorized.

        .. rubric:: Example

        .. code-block:: python

            me = await client.get_me()
            assert me is not None, "not logged in!"

            if me.bot:
                print('I am a bot')

            print('My name is', me.full_name)

            if me.phone:
                print('My phone number is', me.phone)
        """
        return await get_me(self)

    def get_messages(self, chat: ChatLike, limit: Optional[int]=None, *, offset_id: Optional[int]=None, offset_date: Optional[datetime.datetime]=None) -> AsyncList[Message]:
        """
        Get the message history from a :term:`chat`.

        :param chat:
            The :term:`chat` where the message to edit is.

        :param limit:
            How many messages to fetch at most.

        :param offset_id:
            Start getting messages with an identifier lower than this one.
            This means only messages older than the message with ``id = offset_id`` will be fetched.

        :param offset_date:
            Start getting messages with a date lower than this one.
            This means only messages sent before *offset_date* will be fetched.

        :return: The message history.

        .. rubric:: Example

        .. code-block:: python

            # Get the last message in a chat
            last_message = (await client.get_messages(chat, 1))[0]

            # Print all messages before 2023 as HTML
            from datetime import datetime

            async for message in client.get_messages(chat, offset_date=datetime(2023, 1, 1)):
                print(message.sender.full_name, ':', message.html_text)
        """
        return get_messages(self, chat, limit, offset_id=offset_id, offset_date=offset_date)

    def get_messages_with_ids(self, chat: ChatLike, message_ids: List[int]) -> AsyncList[Message]:
        return get_messages_with_ids(self, chat, message_ids)

    def get_participants(self, chat: ChatLike) -> AsyncList[Participant]:
        """
        Get the participants in a group or channel, along with their permissions.

        Note that Telegram is rather strict when it comes to fetching members.
        It is very likely that you will not be able to fetch all the members.
        There is no way to bypass this.

        :return: The participants.

        .. rubric:: Example

        .. code-block:: python

            async for participant in client.get_participants(chat):
                print(participant.user.full_name)
        """
        return get_participants(self, chat)

    def get_profile_photos(self, chat: ChatLike) -> AsyncList[File]:
        """
        Get the profile pictures set in a chat, or user avatars.

        :return: The photo files.

        .. rubric:: Example

        .. code-block:: python

            i = 0
            async for photo in client.get_profile_photos(chat):
                await client.download(photo, f'{i}.jpg')
                i += 1
        """
        return get_profile_photos(self, chat)

    async def inline_query(self, bot: ChatLike, query: str='', *, chat: Optional[ChatLike]=None) -> AsyncIterator[InlineResult]:
        """
        Perform a *@bot inline query*.

        It's known as inline because clients with a GUI display the results *inline*,
        after typing on the message input textbox, without sending any message.

        :param bot:
            The bot to sent the query string to.

        :param query:
            The query string to send to the bot.

        :param chat:
            Where the query is being made and will be sent.
            Some bots display different results based on the type of chat.

        :return: The query results returned by the bot.

        .. rubric:: Example

        .. code-block:: python

            i = 0

            # This is equivalent to typing "@bot songs" in an official client
            async for result in client.inline_query(bot, 'songs'):
                if 'keyword' in result.title:
                    await result.send(chat)
                    break

                i += 1
                if i == 10:
                    break  # did not find 'keyword' in the first few results
        """
        return await inline_query(self, bot, query, chat=chat)

    async def interactive_login(self, phone_or_token: Optional[str]=None, *, password: Optional[str]=None) -> User:
        """
        Begin an interactive login if needed.
        If the account was already logged-in, this method simply returns :term:`yourself`.

        :param phone_or_token:
            Bypass the phone number or bot token prompt, and use this value instead.

        :param password:
            Bypass the 2FA password prompt, and use this value instead.

        :return: The user corresponding to :term:`yourself`.

        .. rubric:: Example

        .. code-block:: python

            me = await client.interactive_login()
            print('Logged in as:', me.full_name)

            # or, to make sure you're logged-in as a bot
            await client.interactive_login('1234:ab56cd78ef90)

        .. seealso::

            In-depth explanation for :doc:`/basic/signing-in`.
        """
        return await interactive_login(self, phone_or_token, password=password)

    async def is_authorized(self) -> bool:
        """
        Check whether the client instance is authorized (i.e. logged-in).

        :return: :data:`True` if the client instance has signed-in.

        .. rubric:: Example

        .. code-block:: python

            if not await client.is_authorized():
                ...  # need to sign in
        """
        return await is_authorized(self)

    def on(self, event_cls: Type[Event], filter: Optional[Filter]=None) -> Callable[[Callable[[Event], Awaitable[Any]]], Callable[[Event], Awaitable[Any]]]:
        """
        Register the decorated function to be invoked when the provided event type occurs.

        :param event_cls:
            The event type to bind to the handler.
            When Telegram sends an update corresponding to this type,
            the decorated function is called with an instance of this event type as the only argument.

        :param filter:
            Filter function to call with the event before calling *handler*.
            If it returns `False`, *handler* will not be called.
            See the :mod:`~telethon.events.filters` module to learn more.

        :return: The decorator.

        .. rubric:: Example

        .. code-block:: python

            # Register a handler to be called on new messages
            @client.on(events.NewMessage)
            async def my_print_handler(event):
                print(event.chat.full_name, event.text)

            # Register a handler to be called on new messages if they contain "hello" or "/start"
            from telethon.events.filters import Any, Text, Command

            @client.on(events.NewMessage, Any(Text(r'hello'), Command('/start')))
            async def my_other_print_handler(event):
                print(event.chat.full_name, event.text)

        .. seealso::

            :meth:`add_event_handler`, used to register existing functions as event handlers.
        """
        return on(self, event_cls, filter)

    async def pin_message(self, chat: ChatLike, message_id: int) -> Message:
        """
        Pin a message to be at the top.

        :param chat:
            The :term:`chat` where the message to pin is.

        :param message_id:
            The identifier of the message to pin.

        :return: The service message announcing the pin.

        .. rubric:: Example

        .. code-block:: python

            # Pin a message, then delete the service message
            message = await client.pin_message(chat, 187481)
            await message.delete()
        """
        return await pin_message(self, chat, message_id)

    def remove_event_handler(self, handler: Callable[[Event], Awaitable[Any]]) -> None:
        """
        Remove the handler as a function to be called when events occur.
        This is simply the opposite of :meth:`add_event_handler`.
        Does nothing if the handler was not actually registered.

        :param handler:
            The callable to stop invoking when events occur.

        .. rubric:: Example

        .. code-block:: python

            # Register a handler that removes itself when it receives 'stop'
            @client.on(events.NewMessage)
            async def my_handler(event):
                if 'stop' in event.text:
                    client.remove_event_handler(my_handler)
                else:
                    print('still going!')

        .. seealso::

            :meth:`add_event_handler`, used to register existing functions as event handlers.
        """
        remove_event_handler(self, handler)

    async def request_login_code(self, phone: str) -> LoginToken:
        """
        Request Telegram to send a login code to the provided phone number.
        This is simply the opposite of :meth:`add_event_handler`.
        Does nothing if the handler was not actually registered.

        :param phone:
            The phone number string, in international format.
            The plus-sign ``+`` can be kept in the string.

        :return: Information about the sent code.

        .. rubric:: Example

        .. code-block:: python

            login_token = await client.request_login_code('+1 23 456...')
            print(login_token.timeout, 'seconds before code expires')

        .. seealso::

            :meth:`sign_in`, to complete the login procedure.
        """
        return await request_login_code(self, phone)

    async def resolve_to_packed(self, chat: ChatLike) -> PackedChat:
        """
        Resolve a :term:`chat` and return a compact, reusable reference to it.

        :param chat:
            The :term:`chat` to resolve.

        :return: An efficient, reusable version of the input.

        .. rubric:: Example

        .. code-block:: python

            friend = await client.resolve_to_packed('@cat')
            # Now you can use `friend` to get or send messages, files...

        .. seealso::

            In-depth explanation for :doc:`/concepts/chats`.
        """
        return await resolve_to_packed(self, chat)

    async def resolve_username(self, username: str) -> Chat:
        return await resolve_username(self, username)

    async def run_until_disconnected(self) -> None:
        await run_until_disconnected(self)

    def search_all_messages(self, limit: Optional[int]=None, *, query: Optional[str]=None, offset_id: Optional[int]=None, offset_date: Optional[datetime.datetime]=None) -> AsyncList[Message]:
        """
        Perform a global message search.
        This is used to search messages in no particular chat (i.e. everywhere possible).

        :param chat:
            The :term:`chat` where the message to edit is.

        :param limit:
            How many messages to fetch at most.

        :param query:
            Text query to use for fuzzy matching messages.
            The rules for how "fuzzy" works are an implementation detail of the server.

        :param offset_id:
            Start getting messages with an identifier lower than this one.
            This means only messages older than the message with ``id = offset_id`` will be fetched.

        :param offset_date:
            Start getting messages with a date lower than this one.
            This means only messages sent before *offset_date* will be fetched.

        :return: The found messages.

        .. rubric:: Example

        .. code-block:: python

            async for message in client.search_all_messages(query='hello'):
                print(message.text)
        """
        return search_all_messages(self, limit, query=query, offset_id=offset_id, offset_date=offset_date)

    def search_messages(self, chat: ChatLike, limit: Optional[int]=None, *, query: Optional[str]=None, offset_id: Optional[int]=None, offset_date: Optional[datetime.datetime]=None) -> AsyncList[Message]:
        """
        Search messages in a chat.

        :param chat:
            The :term:`chat` where messages will be searched.

        :param limit:
            How many messages to fetch at most.

        :param query:
            Text query to use for fuzzy matching messages.
            The rules for how "fuzzy" works are an implementation detail of the server.

        :param offset_id:
            Start getting messages with an identifier lower than this one.
            This means only messages older than the message with ``id = offset_id`` will be fetched.

        :param offset_date:
            Start getting messages with a date lower than this one.
            This means only messages sent before *offset_date* will be fetched.

        :return: The found messages.

        .. rubric:: Example

        .. code-block:: python

            async for message in client.search_messages(chat, query='hello'):
                print(message.text)
        """
        return search_messages(self, chat, limit, query=query, offset_id=offset_id, offset_date=offset_date)

    async def send_audio(self, chat: ChatLike, path: Optional[Union[str, Path, File]]=None, *, url: Optional[str]=None, file: Optional[InFileLike]=None, size: Optional[int]=None, name: Optional[str]=None, duration: Optional[float]=None, voice: bool=False, title: Optional[str]=None, performer: Optional[str]=None, caption: Optional[str]=None, caption_markdown: Optional[str]=None, caption_html: Optional[str]=None) -> Message:
        """
        Send an audio file.

        Unlike :meth:`send_file`, this method will attempt to guess the values for
        duration, title and performer if they are not provided.

        :param chat:
            The :term:`chat` where the message will be sent to.

        :param path:
            A local file path or :class:`~telethon.types.File` to send.

        The rest of parameters behave the same as they do in :meth:`send_file`.

        .. rubric:: Example

        .. code-block:: python

            await client.send_audio(chat, 'file.ogg', voice=True)
        """
        return await send_audio(self, chat, path, url=url, file=file, size=size, name=name, duration=duration, voice=voice, title=title, performer=performer, caption=caption, caption_markdown=caption_markdown, caption_html=caption_html)

    async def send_file(self, chat: ChatLike, path: Optional[Union[str, Path, File]]=None, *, url: Optional[str]=None, file: Optional[InFileLike]=None, size: Optional[int]=None, name: Optional[str]=None, mime_type: Optional[str]=None, compress: bool=False, animated: bool=False, duration: Optional[float]=None, voice: bool=False, title: Optional[str]=None, performer: Optional[str]=None, emoji: Optional[str]=None, emoji_sticker: Optional[str]=None, width: Optional[int]=None, height: Optional[int]=None, round: bool=False, supports_streaming: bool=False, muted: bool=False, caption: Optional[str]=None, caption_markdown: Optional[str]=None, caption_html: Optional[str]=None) -> Message:
        """
        Send any type of file with any amount of attributes.

        This method will *not* attempt to guess any of the file metadata such as width, duration, title, etc.
        If you want to let the library attempt to guess the file metadata, use the type-specific methods to send media:
        `send_photo`, `send_audio` or `send_file`.

        Unlike `send_photo`, image files will be sent as documents by default.

        :param chat:
            The :term:`chat` where the message will be sent to.

        :param path:
            A local file path or :class:`~telethon.types.File` to send.

        :param caption:
            Caption text to display under the media, with no formatting.

        :param caption_markdown:
            Caption text to display under the media, parsed as markdown.

        :param caption_html:
            Caption text to display under the media, parsed as HTML.

        The rest of parameters are passed to :meth:`telethon.types.File.new`
        if *path* isn't a :class:`~telethon.types.File`.
        See the documentation of :meth:`~telethon.types.File.new` to learn what they do.

        See the section on :doc:`/concepts/messages` to learn about message formatting.

        Note that only one *caption* parameter can be provided.

        .. rubric:: Example

        .. code-block:: python

            login_token = await client.request_login_code('+1 23 456...')
            print(login_token.timeout, 'seconds before code expires')
        """
        return await send_file(self, chat, path, url=url, file=file, size=size, name=name, mime_type=mime_type, compress=compress, animated=animated, duration=duration, voice=voice, title=title, performer=performer, emoji=emoji, emoji_sticker=emoji_sticker, width=width, height=height, round=round, supports_streaming=supports_streaming, muted=muted, caption=caption, caption_markdown=caption_markdown, caption_html=caption_html)

    async def send_message(self, chat: ChatLike, text: Optional[Union[str, Message]]=None, *, markdown: Optional[str]=None, html: Optional[str]=None, link_preview: Optional[bool]=None, reply_to: Optional[int]=None) -> Message:
        """
        Send a message.

        :param chat:
            The :term:`chat` where the message will be sent to.

        :param text:
            Message text, with no formatting.

            When given a :class:`Message` instance, a copy of the message will be sent.

        :param text_markdown:
            Message text, parsed as CommonMark.

        :param text_html:
            Message text, parsed as HTML.

        :param link_preview:
            Whether the link preview is allowed.

            Setting this to :data:`True` does not guarantee a preview.
            Telegram must be able to generate a preview from the first link in the message text.

            To regenerate the preview, send the link to `@WebpageBot <https://t.me/WebpageBot>`_.

        :param reply_to:
            The message identifier of the message to reply to.

        Note that exactly one *text* parameter must be provided.

        See the section on :doc:`/concepts/messages` to learn about message formatting.

        .. rubric:: Example

        .. code-block:: python

            await client.send_message(chat, markdown='**Hello!**')
        """
        return await send_message(self, chat, text, markdown=markdown, html=html, link_preview=link_preview, reply_to=reply_to)

    async def send_photo(self, chat: ChatLike, path: Optional[Union[str, Path, File]]=None, *, url: Optional[str]=None, file: Optional[InFileLike]=None, size: Optional[int]=None, name: Optional[str]=None, compress: bool=True, width: Optional[int]=None, height: Optional[int]=None, caption: Optional[str]=None, caption_markdown: Optional[str]=None, caption_html: Optional[str]=None) -> Message:
        """
        Send a photo file.

        By default, the server will be allowed to `compress` the image.
        Only compressed images can be displayed as photos in applications.
        If *compress* is set to :data:`False`, the image will be sent as a file document.

        Unlike `send_file`, this method will attempt to guess the values for
        width and height if they are not provided.

        :param chat:
            The :term:`chat` where the message will be sent to.

        :param path:
            A local file path or :class:`~telethon.types.File` to send.

        The rest of parameters behave the same as they do in :meth:`send_file`.

        .. rubric:: Example

        .. code-block:: python

            await client.send_photo(chat, 'photo.jpg', caption='Check this out!')
        """
        return await send_photo(self, chat, path, url=url, file=file, size=size, name=name, compress=compress, width=width, height=height, caption=caption, caption_markdown=caption_markdown, caption_html=caption_html)

    async def send_video(self, chat: ChatLike, path: Optional[Union[str, Path, File]]=None, *, url: Optional[str]=None, file: Optional[InFileLike]=None, size: Optional[int]=None, name: Optional[str]=None, duration: Optional[float]=None, width: Optional[int]=None, height: Optional[int]=None, round: bool=False, supports_streaming: bool=False, caption: Optional[str]=None, caption_markdown: Optional[str]=None, caption_html: Optional[str]=None) -> Message:
        """
        Send a video file.

        Unlike `send_file`, this method will attempt to guess the values for
        duration, width and height if they are not provided.

        :param chat:
            The :term:`chat` where the message will be sent to.

        :param path:
            A local file path or :class:`~telethon.types.File` to send.

        The rest of parameters behave the same as they do in :meth:`send_file`.

        .. rubric:: Example

        .. code-block:: python

            await client.send_video(chat, 'video.mp4', caption_markdown='*I cannot believe this just happened*')
        """
        return await send_video(self, chat, path, url=url, file=file, size=size, name=name, duration=duration, width=width, height=height, round=round, supports_streaming=supports_streaming, caption=caption, caption_markdown=caption_markdown, caption_html=caption_html)

    def set_admin_rights(self, chat: ChatLike, user: ChatLike) -> None:
        set_admin_rights(self, chat, user)

    def set_banned_rights(self, chat: ChatLike, user: ChatLike) -> None:
        set_banned_rights(self, chat, user)

    def set_default_rights(self, chat: ChatLike, user: ChatLike) -> None:
        set_default_rights(self, chat, user)

    def set_handler_filter(self, handler: Callable[[Event], Awaitable[Any]], filter: Optional[Filter]=None) -> None:
        """
        Set the filter to use for the given event handler.

        :param handler:
            The callable that was previously added as an event handler.

        :param filter:
            The filter to use for *handler*, or :data:`None` to remove the old filter.

        .. rubric:: Example

        .. code-block:: python

            from telethon.events import filters

            # Change the filter to handle '/stop'
            client.set_handler_filter(my_handler, filters.Command('/stop'))

            # Remove the filter
            client.set_handler_filter(my_handler, None)
        """
        set_handler_filter(self, handler, filter)

    async def sign_in(self, token: LoginToken, code: str) -> Union[User, PasswordToken]:
        """
        Sign in to a user account.

        :param token:
            The login token returned from :meth:`request_login_code`.

        :return:
            The user corresponding to :term:`yourself`, or a password token if the account has 2FA enabled.

        .. rubric:: Example

        .. code-block:: python

            from telethon.types import PasswordToken

            login_token = await client.request_login_code('+1 23 456')
            user_or_token = await client.sign_in(login_token, input('code: '))

            if isinstance(password_token, PasswordToken):
                user = await client.check_password(password_token, '1-L0V3+T3l3th0n')

        .. seealso::

            :meth:`check_password`, the next step if the account has 2FA enabled.
        """
        return await sign_in(self, token, code)

    async def sign_out(self) -> None:
        """
        Sign out, revoking the authorization of the current :term:`session`.

        .. rubric:: Example

        .. code-block:: python

            await client.sign_out()  # turn off the lights
            await client.disconnect()  # shut the door
        """
        await sign_out(self)

    async def unpin_message(self, chat: ChatLike, message_id: Union[int, Literal['all']]) -> None:
        """
        Unpin one or all messages from the top.

        :param chat:
            The :term:`chat` where the message pinned message is.

        :param message_id:
            The identifier of the message to unpin, or ``'all'`` to unpin them all.

        .. rubric:: Example

        .. code-block:: python

            # Unpin all messages
            await client.unpin_message(chat, 'all')
        """
        await unpin_message(self, chat, message_id)

    # End partially @generated

    @property
    def connected(self) -> bool:
        return connected(self)

    def _build_message_map(
        self,
        result: abcs.Updates,
        peer: Optional[abcs.InputPeer],
    ) -> MessageMap:
        return build_message_map(self, result, peer)

    async def _resolve_to_packed(self, chat: ChatLike) -> PackedChat:
        return await resolve_to_packed(self, chat)

    def _input_to_peer(self, input: Optional[abcs.InputPeer]) -> Optional[abcs.Peer]:
        return input_to_peer(self, input)

    async def __call__(self, request: Request[Return]) -> Return:
        if not self._sender:
            raise ConnectionError("not connected")

        return await invoke_request(self, self._sender, self._sender_lock, request)

    async def __aenter__(self) -> Self:
        await connect(self)
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        exc_type, exc, tb
        await disconnect(self)
