"""
    Skelenox: the collaborative IDA Pro Agent

    This file is part of Polichombr
        (c) ANSSI-FR 2017
"""

import os
import time
import httplib
import gzip
import atexit
import json
import logging
import threading
import datetime

from StringIO import StringIO
from string import lower

from idaapi import *
from idautils import *
from idc import *

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QWidget, QVBoxLayout


g_logger = logging.getLogger()
for h in g_logger.handlers:
    g_logger.removeHandler(h)

g_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
format_str = '[%(asctime)s] [%(levelname)s] [%(threadName)s]: %(message)s'
formatter = logging.Formatter(format_str, datefmt='%d/%m/%Y %I:%M')
handler.setFormatter(formatter)
g_logger.addHandler(handler)


class SkelConfig(object):
    """
        Config management
    """

    def __init__(self, settings_file):
        filename = os.path.dirname(__file__) + "/" + settings_file
        self.username = "Anonymous"
        self.edit_flag = False

        # Network config
        self.poli_server = ""
        self.poli_port = 80
        self.poli_remote_path = ""
        self.poli_apikey = ""
        self.debug_http = False

        # Skelenox general config
        self.save_timeout = 10 * 60

        if os.path.isfile(filename):
            g_logger.info("Loading settings file")
            self._do_init(filename)
        else:
            g_logger.warning("Config file not edited, populating default")
            self.populate_default(filename)
            self.not_edited(filename)

    @staticmethod
    def not_edited(filename):
        """
            The user have not edited the settings
        """
        idc.Warning("Please edit the %s file with your settings!" % (filename))
        raise EnvironmentError

    def _do_init(self, filename):
        """
            Loads the settings in JSON file
        """
        with open(filename, 'r') as inputfile:
            raw_data = inputfile.read()
            data = json.loads(raw_data, encoding='ascii')
            if data["edit_flag"] is False:
                self.not_edited(filename)
            else:
                for key in data.keys():
                    setattr(self, key, data[key])

    def populate_default(self, filename):
        """
            Dumps the default value in JSON in the given filename
        """
        data = json.dumps(vars(self), sort_keys=True, indent=4)
        with open(filename, 'w') as outfile:
            outfile.write(data)

    def dump_config(self):
        """
            Simply print the config on screen
        """
        values = {}
        for elem in vars(self).keys():
            values[elem] = vars(self)[elem]
        g_logger.info(json.dumps(values, sort_keys=True, indent=4))


class SkelConnection(object):
    """
        HTTP(S) API management
    """
    sample_id = None
    remote_path = None
    http_debug = None
    api_key = None
    poli_server = None
    poli_port = None
    h_conn = None
    is_online = False

    def __init__(self, skel_config=None):
        """
            Here skel_config should be a SkelConfig object
        """
        if skel_config is None:
            raise ValueError
        self.http_debug = skel_config.debug_http
        self.remote_path = skel_config.poli_remote_path
        self.api_key = skel_config.poli_apikey
        self.poli_server = skel_config.poli_server
        self.poli_port = skel_config.poli_port

        self.h_conn = None
        self.is_online = False
        self.sample_id = None

    def get_online(self):
        """
            Connect to the server
        """
        try:
            self.__do_init()
        except Exception:
            g_logger.exception("The polichombr server seems down")
            return False
        return True

    def __do_init(self):
        """
            Initiate connection handle
        """
        if self.http_debug is True:
            g_logger.info("Connecting using simple HTTP")
        else:
            g_logger.error("HTTPS is not managed at the moment...")

        self.h_conn = httplib.HTTPConnection(self.poli_server, self.poli_port)
        self.h_conn.connect()
        self.is_online = True
        self.init_sample_id()

    def close_connection(self):
        """
            Cleanup the connection
        """
        g_logger.debug("Closing connection")
        if self.h_conn is not None:
            self.h_conn.close()
        self.is_online = False
        self.sample_id = None

    def poli_request(self, endpoint, data, method="POST"):
        """
            @arg : endpoint The API target endpoint
            @arg : data dictionary
            @return : dict issued from JSON
        """
        if not self.is_online:
            g_logger.error("Cannot send requests while not connected")
            raise IOError
        headers = {"Accept-encoding": "gzip, deflate",
                   "Content-type": "application/json",
                   "Accept": "*/*;q=0.8",
                   "Accept-Language": "en-US,en;q=0.5",
                   "Connection": "Keep-Alive",
                   "X-API-Key": self.api_key}
        json_data = json.dumps(data)
        try:
            self.h_conn.request(method, endpoint, json_data, headers)
        except httplib.CannotSendRequest as e:
            g_logger.error("Error during request, retrying")
            self.close_connection()
            self.get_online()
            self.h_conn.request(method, endpoint, json_data, headers)
        res = self.h_conn.getresponse()

        if res.status != 200:
            g_logger.error("The %s request didn't go as expected", method)
        content_type = res.getheader("Content-Encoding")
        if content_type == "gzip":
            buf = StringIO(res.read())
            res = gzip.GzipFile(fileobj=buf)
        data = res.read()
        try:
            result = json.loads(data)
        except:
            raise IOError
        return result

    def poli_post(self, endpoint="/", data=None):
        result = self.poli_request(endpoint, data, method='POST')
        return result

    def poli_get(self, endpoint="/", data=None):
        result = self.poli_request(endpoint, data, method='GET')
        return result

    def poli_put(self, endpoint="/", data=None):
        result = self.poli_request(endpoint, data, method='PUT')
        return result

    def poli_delete(self, endpoint='/', data=None):
        result = self.poli_request(endpoint, data, method='DELETE')
        return result

    def poli_patch(self, endpoint='/', data=None):
        result = self.poli_request(endpoint, data, method='PATCH')
        return result

    def push_comment(self, address=0, comment=None):
        """
            Push a standard comment
        """
        if comment is None:
            return False
        data = {"address": address,
                "comment": comment}
        endpoint = self.prepare_endpoint('comments')
        res = self.poli_post(endpoint, data)
        if res["result"]:
            g_logger.debug("Comment %s sent for address 0x%x", comment, address)
        else:
            g_logger.error("Cannot send comment %s ( 0x%x )", comment, address)
        return res["result"]

    def push_type(self, address, mtype=None):
        """
            Push defined types, parsed with prepare_parse_type
        """
        data = {"address": address,
                "type": mtype}
        endpoint = self.prepare_endpoint('types')
        res = self.poli_post(endpoint, data)
        if res["result"]:
            g_logger.debug("New type %s sent for address 0x%x", mtype, address)
        else:
            g_logger.error("Cannot send type %s ( 0x%x )", mtype, address)
        return res["result"]

    def get_abstract(self):
        endpoint = self.prepare_endpoint("abstract")
        abstract = self.poli_get(endpoint)
        return abstract["abstract"]

    def push_abstract(self, abstract):
        endpoint = self.prepare_endpoint("abstract")
        data = {"abstract": abstract}
        res = self.poli_post(endpoint, data)
        if res["result"]:
            g_logger.debug("Abstract sent!")
        else:
            g_logger.error("Cannot send abstract...\n Error %s", res)

    def send_sample(self, filedata):
        """
            Ugly wrapper for uploading a file in multipart/form-data
        """
        endpoint = "/api/1.0/samples/"
        headers = {"Accept-encoding": "gzip, deflate",
                   "X-API-Key": self.api_key}

        method = "POST"
        boundary = "70f6e331562f4b8f98e5f9590e0ffb8e"
        headers["Content-type"] = "multipart/form-data; boundary="+boundary
        body = "--" + boundary
        body += "\r\n"
        body += "Content-Disposition: form-data; name=\"filename\"\r\n"
        body += "\r\n"
        body += idc.GetInputFile()
        body += "\r\n\r\n"
        body += "--" + boundary + "\r\n"

        body += "Content-Disposition: form-data;"
        body += "name=\"file\"; filename=\"file\"\r\n"
        body += "\r\n"
        body += filedata.read()
        body += "\r\n--"
        body += boundary
        body += "--\r\n"

        self.h_conn.request(method, endpoint, body, headers)
        res = self.h_conn.getresponse()
        data = res.read()
        try:
            result = json.loads(data)
        except:
            g_logger.exception("Cannot load json data from server")
            result = None
        return result

    def get_sample_id(self):
        """
            Query the server for the sample ID
        """
        endpoint = "/api/1.0/samples/"
        endpoint += lower(GetInputMD5())
        endpoint += "/"
        try:
            data = self.poli_get(endpoint)
            if data["sample_id"] is not None:
                return data["sample_id"]
            else:
                return False
        except:  # 404?
            return False

    def init_sample_id(self):
        """
            test if the remote sample exists,
            if not, we upload it
        """
        if self.sample_id is None:
            self.sample_id = self.get_sample_id()
            if not self.sample_id:
                g_logger.warning("Sample not found on server, uploading it")
                self.send_sample(open(idc.GetInputFile(), 'rb'))
                self.sample_id = self.get_sample_id()
                g_logger.info("Sample ID: %d", self.sample_id)

    def get_comments(self, timestamp=None):
        endpoint = self.prepare_endpoint('comments')
        format_ts = "%Y-%m-%dT%H:%M:%S.%f"
        if timestamp is not None:
            endpoint += "?timestamp="
            endpoint += datetime.datetime.strftime(timestamp, format_ts)
        res = self.poli_get(endpoint)
        return res["comments"]

    def get_names(self, timestamp=None):
        """
            Get all names defined in the database
        """
        endpoint = self.prepare_endpoint('names')
        format_ts = "%Y-%m-%dT%H:%M:%S.%f"
        if timestamp is not None:
            endpoint += "?timestamp="
            endpoint += datetime.datetime.strftime(timestamp, format_ts)
        res = self.poli_get(endpoint)
        return res["names"]

    def get_proposed_names(self):
        """
            Get machoc proposed names
            Returns a list of dictionaries by address
        """
        endpoint = self.prepare_endpoint("functions/proposednames")
        res = self.poli_get(endpoint)
        return res["functions"]

    def push_name(self, address=0, name=None):
        """
            Send a define name, be it func or area
        """
        if name is None:
            return False
        data = {"address": address,
                "name": name}
        endpoint = self.prepare_endpoint('names')
        res = self.poli_post(endpoint, data)
        if res["result"]:
            g_logger.debug("sent name %s at 0x%x", name, address)
        else:
            g_logger.error("failed to send name %s", name)
        return True

    def create_struct(self, struct_name):
        """
            Create a structure in the database
            @arg:
                the structure name
            @return:
                The struct id, False if failed
        """
        endpoint = self.prepare_endpoint('structs')
        data = dict(name=struct_name)
        res = self.poli_post(endpoint, data)
        if not res["result"]:
            return False
        sid = res["structs"][0]["id"]
        return sid

    def create_struc_member(self, struct_id, start_offset):
        """
        XXX :
            [ ] Get struct id from name
        """
        endpoint = self.prepare_endpoint('structs')
        return False

    def prepare_endpoint(self, submodule):
        """
            Prepare a standard API endpoint
        """
        endpoint = self.remote_path + "samples/"
        endpoint += str(self.sample_id)
        endpoint += "/" + submodule + "/"
        return endpoint


class SkelHooks(object):
    """
        Class containing the three different hooks for skelenox

        SkelUIHook :
            * Original UI hook, catch cmds.
            Drawbacks : doesn't catch the actions made by scripts

        SkelIDBHook:
            * Catches the main actions
            Drawbacks:
                - type info management
                - doesn't catch naming actions
        SkelIDPHook:
            * IDPHook used for the actions not implemented in IDBHooks

    """
    ui_hook = None
    idb_hook = None
    idp_hook = None

    class SkelUIHook(idaapi.UI_Hooks):
        """
            Catch IDA UI actions and send them
        """
        cmdname = ""
        addr = 0
        skel_conn = None

        def __init__(self, skel_conn):
            idaapi.UI_Hooks.__init__(self)
            self.skel_conn = skel_conn

        def preprocess(self, name):
            self.cmdname = name
            self.addr = idc.here()
            return 0

        def term(self):
            end_skelenox()

        def postprocess(self):
            try:
                if "MakeComment" in self.cmdname:
                    if idc.Comment(self.addr) is not None:
                        self.skel_conn.push_comment(
                            self.addr, idc.Comment(self.addr))
                    if idc.GetFunctionCmt(self.addr, 0) != "":
                        self.skel_conn.push_comment(
                            self.addr, idc.GetFunctionCmt(
                                (self.addr), 0))
                elif "MakeRptCmt" in self.cmdname:
                    if idc.GetCommentEx(self.addr, 1) != "":
                        self.skel_conn.push_comment(self.addr,
                                                    idc.GetCommentEx(self.addr, 1))
                    if idc.GetFunctionCmt(self.addr, 1) != "":
                        self.skel_conn.push_comment(self.addr,
                                                    idc.GetFunctionCmt(self.addr, 1))

                elif self.cmdname == "MakeFunction":
                    if idc.GetFunctionAttr(self.addr, 0) is not None:
                        # Push "MakeFunction" change
                        pass
                elif self.cmdname == "DeclareStructVar":
                    g_logger.error("Fixme : declare Struct variable")
                elif self.cmdname == "SetType":
                    newtype = idc.GetType(self.addr)
                    if newtype is None:
                        newtype = ""
                    else:
                        newtype = SkelUtils.prepare_parse_type(newtype, self.addr)
                        self.skel_conn.push_type(int(self.addr), newtype)
                    # XXX IMPLEMENT
                elif self.cmdname == "OpStructOffset":
                    g_logger.debug("A struct member is typed to struct offset")
            except KeyError:
                pass
            return 0

    class SkelIDBHook(idaapi.IDB_Hooks):
        """
            IDB hooks, subclassed from ida_idp.py
        """
        skel_conn = None

        def __init__(self, skel_conn):
            idaapi.IDB_Hooks.__init__(self)
            self.skel_conn = skel_conn

        def cmt_changed(self, *args):
            """
                A comment changed somewhere
            """
            addr, rpt = args
            if rpt:
                cmt = RptCmt(addr)
            else:
                cmt = Comment(addr)
            if not SkelUtils.filter_coms_blacklist(cmt):
                self.skel_conn.push_comment(addr, cmt)
            return idaapi.IDB_Hooks.cmt_changed(self, *args)

        def struc_created(self, *args):
            """
                args -> id
            """
            struct_name = idaapi.get_struc_name(args[0])
            self.skel_conn.create_struct(struct_name)

            g_logger.debug("New structure %s created", struct_name)

            return idaapi.IDB_Hooks.struc_created(self, *args)

        def struc_member_created(self, *args):
            """
                struc_member_created(self, sptr, mptr) -> int
            """
            sptr, mptr = args
            m_start_offset = mptr.soff
            m_end_offset = mptr.eoff

            return idaapi.IDB_Hooks.struc_member_created(self, *args)

        def deleting_struc(self, *args):
            """
            deleting_struc(self, sptr) -> int
            """
            # print "DELETING STRUCT"
            return idaapi.IDB_Hooks.deleting_struc(self, *args)

        def renaming_struc(self, *args):
            """
            renaming_struc(self, id, oldname, newname) -> int
            """
            #print "RENAMING STRUCT"
            #print args
            return idaapi.IDB_Hooks.renaming_struc(self, *args)

        def expanding_struc(self, *args):
            """
            expanding_struc(self, sptr, offset, delta) -> int
            """
            return idaapi.IDB_Hooks.expanding_struc(self, *args)

        def changing_struc_cmt(self, *args):
            """
            changing_struc_cmt(self, struc_id, repeatable, newcmt) -> int
            """
            return idaapi.IDB_Hooks.changing_struc_cmt(self, *args)

        def deleting_struc_member(self, *args):
            """
            deleting_struc_member(self, sptr, mptr) -> int
            """
            return idaapi.IDB_Hooks.deleting_struc_member(self, *args)

        def renaming_struc_member(self, *args):
            """
            renaming_struc_member(self, sptr, mptr, newname) -> int
            """
            # print "RENAMING STRUCT MEMBER"
            #print args
            mystruct, mymember, newname = args
            #print mymember
            #print dir(mymember)
            return idaapi.IDB_Hooks.renaming_struc_member(self, *args)

        def changing_struc_member(self, *args):
            """
            changing_struc_member(self, sptr, mptr, flag, ti, nbytes) -> int
            """
            #print "CHANGING STRUCT MEMBER"
            #print args
            mystruct, mymember, flag, ti, nbytes = args
            #print ti
            #print dir(ti)
            #print ti.cd
            #print ti.ec
            #print ti.ri
            #print ti.tid
            return idaapi.IDB_Hooks.changing_struc_member(self, *args)

        def op_type_changed(self, *args):
            #print args
            return idaapi.IDB_Hooks.op_type_changed(self, *args)

    class SkelIDPHook(idaapi.IDP_Hooks):
        """
            Hook IDP that saves the database regularly
        """
        skel_conn = None

        def __init__(self, skel_conn):
            idaapi.IDP_Hooks.__init__(self)
            self.skel_conn = skel_conn

        def renamed(self, *args):
            g_logger.debug("[IDB Hook] Something is renamed")
            ea, new_name, is_local_name = args
            if ea >= idc.MinEA() and ea <= idc.MaxEA():
                if is_local_name:
                    # XXX push_new_local_name(ea, new_name)
                    pass
                else:
                    if not SkelUtils.name_blacklist(new_name):
                        self.skel_conn.push_name(ea, new_name)
            else:
                g_logger.warning("ea outside program...")

            return idaapi.IDP_Hooks.renamed(self, *args)

    def __init__(self, skel_conn):
        self.ui_hook = SkelHooks.SkelUIHook(skel_conn)
        self.idb_hook = SkelHooks.SkelIDBHook(skel_conn)
        self.idp_hook = SkelHooks.SkelIDPHook(skel_conn)

    def hook(self):
        self.ui_hook.hook()
        self.idb_hook.hook()
        self.idp_hook.hook()

    def cleanup_hooks(self):
        """
            Clean IDA hooks on exit
        """
        if self.ui_hook is not None:
            self.ui_hook.unhook()
            self.ui_hook = None

        if self.idb_hook is not None:
            self.idb_hook.unhook()
            self.idb_hook = None

        if self.idp_hook is not None:
            self.idp_hook.unhook()
            self.idp_hook = None
        return


class SkelUtils(object):
    """
        Utils functions
    """

    @staticmethod
    def name_blacklist(name):
        """
            Standard name blacklist
            return True if blacklisted
        """
        default_values = ['sub_', "dword_", "unk_", "byte_", "word_", "loc_"]
        for value in default_values:
            if value in name[:len(value)+1]:
                return True
        return False

    @staticmethod
    def func_name_blacklist(name):
        """
            Blacklist for common function names
            Includes:
                sub_*,
                ?*,
                nullsub*,
                unknown*,
                SEH*
        """
        if name is not None:
            default_values = ['sub_', 'nullsub', 'unknown', 'SEH_',
                              '__imp', 'j_', '__IMP']
            for val in default_values:
                if val in name[:len(val)+1]:
                    return True
            if name[0] == "@" or name[0] == "?":
                return True
        return False

    @staticmethod
    def prepare_parse_type(typestr, addr):
        """
            idc.ParseType doesnt accept types without func / local name
            as exported by default GetType
            this is an ugly hack to fix it
            FIXME : parsing usercall (@<XXX>)
        """
        lname = idc.GetTrueName(addr)
        if lname is None:
            lname = "Default"

        # func pointers
        fpconventions = ["__cdecl *",
                         "__stdcall *",
                         "__fastcall *",
                         #"__usercall *",
                         #"__userpurge *",
                         "__thiscall *"]

        cconventions = ["__cdecl",
                        "__stdcall",
                        "__fastcall",
                        #"__usercall",
                        #"__userpurge",
                        "__thiscall"]

        flag = False
        mtype = None
        for conv in fpconventions:
            if conv in typestr:
                mtype = typestr.replace(conv, conv + lname)
                flag = True

        if not flag:
            # replace prototype
            for conv in cconventions:
                if conv in typestr:
                    mtype = typestr.replace(conv, conv + " " + lname)
                    flag = True
        return mtype

    @staticmethod
    def header():
        """
            help!
        """
        print "-*" * 40
        print "                 SKELENOX "
        print "        This plugin is part of Polichombr"
        print "             (c) ANSSI-FR 2016"
        print "-" * 80
        print "\t Collaborative reverse engineering framework"
        print "Help:"
        print "see   https://www.github.com/anssi-fr/polichombr/docs/"
        print "-*" * 40
        print "\tfile %IDB%_backup_preskel_ contains pre-critical ops IDB backup"
        print "\tfile %IDB%_backup_ contains periodic IDB backups"
        return

    @staticmethod
    def filter_coms_blacklist(cmt):
        """
            These are standards coms, we don't want them in the DB
        """
        if cmt is None:
            g_logger.error("No comment provided to filter_coms")
            return True
        black_list = [
            "size_t", "int", "LPSTR", "char", "char *", "lpString",
            "dw", "lp", "Str", "Dest", "Src", "cch", "Dst", "jumptable", "switch ",
            "unsigned int", "void *", "indirect table for switch statement", "Size"
            "this", "jump table for", "switch jump", "nSize", "hInternet", "hObject",
            "SEH", "Exception handler", "Source", "Size", "Val", "Time",
            "struct", "unsigned __int", "this", "__int32", "void (", "Memory",
            "HINSTANCE", "jumptable"
            ]
        for elem in black_list:
            if cmt.lower().startswith(elem.lower()):
                g_logger.debug("Comment %s has been blacklisted", cmt)
                return True
        return False

    @staticmethod
    def execute_comment(comment):
        """
            Thread safe comment wrapper
        """
        def make_rpt():
            idc.MakeRptCmt(
                comment["address"],
                comment["data"].encode(
                    'ascii',
                    'replace'))
        cmt = Comment(comment["address"])
        if cmt != comment["data"] and RptCmt(comment["address"]) != comment["data"]:
            g_logger.debug("[x] Adding comment %s @ 0x%x ", comment["data"], comment["address"])
            return idaapi.execute_sync(make_rpt, idaapi.MFF_WRITE)
        else:
            pass

    @staticmethod
    def execute_rename(name):
        """
            This is a wrapper to execute the renaming synchronously
        """
        def get_name():
            return idc.GetTrueName(name["address"])

        def make_name(force=False):
            """
                Thread safe renaming wrapper
            """
            def sync_ask_rename():
                rename_flag = 0
                if force or AskYN(rename_flag, "Replace %s by %s" % (get_name(), name["data"])) == 1:
                    g_logger.debug("[x] renaming %s @ 0x%x as %s",
                                   get_name(),
                                   name["address"],
                                   name["data"])
                    idc.MakeName(name["address"], name["data"].encode('ascii', 'ignore'))
            return idaapi.execute_sync(
                sync_ask_rename,
                idaapi.MFF_FAST)
        if get_name().startswith("sub_"):
            make_name(force=True)

        if get_name() != name["data"]:
            make_name()


class SkelSyncAgent(threading.Thread):
    """
        Agent that pulls the server regularly for new infos
    """
    skel_conn = None
    skel_settings = None
    last_timestamp = None
    update_event = None
    kill_event = None
    timer_setup_flag = None
    delay = None

    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self, name=self.__class__.__name__,
                                  args=args, kwargs=kwargs)
        self.update_event = threading.Event()
        self.kill_event = threading.Event()
        self.last_timestamp = datetime.datetime.fromtimestamp(0)
        g_logger.debug("SyncAgent initialized")
        self.timer_setup_flag = False
        self.delay = 1000

    def setup_config(self, settings_filename):
        """
            Initialize connection in the new thread
        """
        self.skel_settings = SkelConfig(settings_filename)
        self.delay = self.skel_settings.sync_frequency
        self.skel_conn = SkelConnection(self.skel_settings)
        self.skel_conn.get_online()

    def sync_names(self):
        """
            Get the remote comments and names
        """
        format_ts = "%Y-%m-%dT%H:%M:%S.%f+00:00"
        if not self.skel_conn.is_online:
            g_logger.error("[!] Error, cannot sync while offline")
            return False

        comments = self.skel_conn.get_comments(timestamp=self.last_timestamp)
        names = self.skel_conn.get_names(timestamp=self.last_timestamp)
        for comment in comments:
            SkelUtils.execute_comment(comment)
            timestamp = datetime.datetime.strptime(comment["timestamp"], format_ts)
            self.last_timestamp = max(timestamp, self.last_timestamp)

        for name in names:
            SkelUtils.execute_rename(name)
            timestamp = datetime.datetime.strptime(name["timestamp"], format_ts)
            self.last_timestamp = max(timestamp, self.last_timestamp)
        return True

    def setup_timer(self):
        """
            Setup an IDA timer to trigger regularly the
            update of data from the server
        """
        def update():
            """
                Triggers the synchronization event
            """
            if not self.update_event.isSet():
                self.update_event.set()
            if self.kill_event.isSet():
                # Unregister the timer if we are killed
                return -1
            return self.delay

        def ts_setup_timer():
            """
                Thread safe wrapper for setting up
                the sync callback
            """
            idaapi.register_timer(self.delay, update)

        if not self.timer_setup_flag:
            idaapi.execute_sync(ts_setup_timer, idaapi.MFF_FAST)
            self.timer_setup_flag = True

    def kill(self):
        """
            Instruct the thread to return
        """
        g_logger.debug("%s exiting", self.__class__.__name__)
        self.kill_event.set()
        # we don't want to wait until the timeout on the update thread,
        # so unlock the update event too
        self.update_event.set()

    def run(self):
        self.setup_timer()
        while True:
            try:
                self.update_event.wait()
                self.update_event.clear()
                timeout = self.skel_settings.sync_frequency
                if self.kill_event.wait(timeout):
                    return 0
                # if we are up, sync names
                self.sync_names()
            except Exception as mye:
                g_logger.exception(mye)
                break


class SkelNotePad(QtWidgets.QWidget):
    """
        Abstract edit widget
    """
    skel_conn = None
    skel_settings = None
    editor = None

    def __init__(self, parent, settings_filename):
        super(SkelNotePad, self).__init__()

        self.skel_settings = SkelConfig(settings_filename)

        self.skel_conn = SkelConnection(self.skel_settings)
        self.skel_conn.get_online()

        self.counter = 0
        self.editor = None
        self.PopulateForm()

    def PopulateForm(self):
        layout = QVBoxLayout()
        label = QtWidgets.QLabel()
        label.setText("Notes about sample %s" % GetInputMD5())

        self.editor = QtWidgets.QTextEdit()

        self.editor.setFontFamily(self.skel_settings.notepad_font_name)
        self.editor.setFontPointSize(self.skel_settings.notepad_font_size)

        text = self.skel_conn.get_abstract()
        self.editor.setPlainText(text)

        # editor.setAutoFormatting(QtWidgets.QTextEdit.AutoAll)
        self.editor.textChanged.connect(self._onTextChange)

        layout.addWidget(label)
        layout.addWidget(self.editor)
        self.setLayout(layout)

    def _onTextChange(self):
        """
        Push the abstract every 10 changes
        """
        self.counter += 1
        remote_text = self.skel_conn.get_abstract()
        diff_len = len(self.editor.toPlainText())
        diff_len -= len(remote_text)
        if diff_len not in range(self.counter+2):
            g_logger.warning("Many changes or remote changes, be aware!")
        if self.counter > 10:
            g_logger.debug("More than 10 changes, pushing abstract")
            text = self.editor.toPlainText()
            self.skel_conn.push_abstract(text)
            self.counter = 0


class SkelFunctionInfosList(QtWidgets.QTableWidget):
    """
        Simple list widget to display proposed names
    """
    class SkelFuncListItem(object):
        def __init__(self,
                     address=None,
                     curname=None,
                     machoc=None,
                     proposed=None
                     ):
            self.address = address
            self.curname = curname
            self.machoc = machoc
            self.proposed = proposed

        def get_widgets(self):
            widgets = {}
            widgets["address"] = QtWidgets.QTableWidgetItem(self.address)
            widgets["curname"] = QtWidgets.QTableWidgetItem(self.curname)
            widgets["machoc"] = QtWidgets.QTableWidgetItem(self.machoc)
            widgets["proposed"] = QtWidgets.QTableWidgetItem(self.proposed)

            return widgets

    def __init__(self, settings_filename):
        super(SkelFunctionInfosList, self).__init__()

        self.config = SkelConfig(settings_filename)
        self.skel_conn = SkelConnection(self.config)
        self.skel_conn.get_online()

        self.init_table()
        self.populate_table()

    def init_table(self):
        """
        Set the initial header
        """
        self.setColumnCount(4)
        self.setRowCount(1)
        labels = ["Address", "Current Name", "machoc", "proposed name"]
        self.setHorizontalHeaderLabels(labels)

    def populate_table(self):
        """
            Download the list of proposed names and display it
        """
        functions = self.skel_conn.get_proposed_names()
        items = []
        for func in functions:
            func_name = GetTrueName(func["address"])
            for name in func["proposed_names"]:
                item = self.SkelFuncListItem(
                        hex(func["address"]),
                        func_name,
                        hex(func["machoc_hash"]),
                        name)
                items.append(item)
        self.setRowCount(len(items))

        for item_index, item in enumerate(items):
            widgets = item.get_widgets()
            self.setItem(item_index, 0, widgets["address"])
            self.setItem(item_index, 1, widgets["curname"])
            self.setItem(item_index, 2, widgets["machoc"])
            self.setItem(item_index, 3, widgets["proposed"])


class SkelFunctionInfos(QtWidgets.QWidget):
    """
        Widgets that displays machoc names for the current sample
    """
    skel_conn = None
    skel_settings = None
    editor = None

    def __init__(self, parent, settings_filename):
        super(SkelFunctionInfos, self).__init__()

        self.skel_settings = SkelConfig(settings_filename)
        self.settings_filename = settings_filename

        self.skel_conn = SkelConnection(self.skel_settings)
        self.skel_conn.get_online()

        self.choose = None
        self.PopulateForm()

    def PopulateForm(self):
        layout = QVBoxLayout()
        label = QtWidgets.QLabel()
        label.setText("Proposed function names for sample %s" % GetInputMD5())

        self.funcinfos = SkelFunctionInfosList(self.settings_filename)

        layout.addWidget(label)
        layout.addWidget(self.funcinfos)
        self.setLayout(layout)


class SkelUI(PluginForm):
    """
        Skelenox UI is contained in a new tab widget.
    """
    def __init__(self, settings_filename):
        super(SkelUI, self).__init__()
        self.parent = None
        self.settings_filename = settings_filename

        self.notepad = None
        self.funcinfos = None

    def OnCreate(self, form):
        g_logger.debug("Called UI initialization")
        self.parent = self.FormToPyQtWidget(form)
        self.PopulateForm()

    def Show(self):
        options = PluginForm.FORM_CLOSE_LATER | PluginForm.FORM_RESTORE | PluginForm.FORM_SAVE
        return PluginForm.Show(self, "Skelenox UI", options=options)

    def PopulateForm(self):
        self.tabs = QtWidgets.QTabWidget()
        layout = QVBoxLayout()
        layout.addWidget(self.tabs)

        self.notepad = SkelNotePad(self, self.settings_filename)
        self.funcinfos = SkelFunctionInfos(self, self.settings_filename)

        self.tabs.addTab(self.notepad, "Notepad")
        self.tabs.addTab(self.funcinfos, "Func Infos")

        self.parent.setLayout(layout)

    def OnClose(self, form):
        g_logger.debug("UI is terminating")

    def Close(self, options=PluginForm.FORM_SAVE):
        super(SkelUI, self).Close(options)


class SkelCore(object):
    """
        This is the main class for skelenox.
        It handles startup, manage agents, connections and so on.
    """
    crit_backup_file = None
    backup_file = None
    last_saved = None
    skel_conn = None
    skel_settings = None
    settings_filename = ""
    skel_hooks = None
    skel_sync_agent = None
    skel_ui = None

    def __init__(self, settings_filename):
        """
            Prepare for execution
        """
        SkelUtils.header()

        g_logger.info("[+] Init Skelenox")

        # Load settings
        self.skel_settings = SkelConfig(settings_filename)

        self.skel_conn = SkelConnection(self.skel_settings)

        # If having 3 idbs in your current path bother you, change this
        self.crit_backup_file = GetIdbPath()[:-4] + "_backup_preskel_.idb"
        self.backup_file = GetIdbPath()[:-4] + "_backup_.idb"

        atexit.register(self.end_skelenox)

        g_logger.info("Backuping IDB before any intervention (_backup_preskel_)")
        SaveBase(self.crit_backup_file, idaapi.DBFL_TEMP)
        g_logger.info("Creating regular backup file IDB (_backup_)")
        SaveBase(self.backup_file, idaapi.DBFL_TEMP)
        self.last_saved = time.time()

        if self.skel_hooks is not None:
            self.skel_hooks.cleanup_hooks()

        if not self.skel_conn.get_online():
            g_logger.error("Cannot get online =(")

        # Synchronize the sample
        self.skel_sync_agent = SkelSyncAgent()
        self.skel_sync_agent.setup_config(settings_filename)
        self.skel_sync_agent.setup_timer()

        # setup hooks
        self.skel_hooks = SkelHooks(self.skel_conn)

        # setup UI
        self.skel_ui = SkelUI(settings_filename)

        # setup skelenox terminator
        self.setup_terminator()

        g_logger.info("Skelenox init finished")

    def send_names(self):
        """
            Used to send all the names to the server.
            Usecase: Previously analyzed IDB
        """
        for head in idautils.Names():
            if not SkelUtils.func_name_blacklist(head[1]):
                mtype = GetType(head[0])
                if mtype and not mtype.lower().startswith("char["):
                    print head[1]
                    self.skel_conn.push_name(head[0], head[1])

    def send_comments(self):
        """
            Initial sync of comments
        """
        for head in Heads():
            com = Comment(head)
            rpt_com = RptCmt(head)
            send_com = ""
            if com and not SkelUtils.filter_coms_blacklist(com):
                send_com += com

            if rpt_com and not SkelUtils.filter_coms_blacklist(rpt_com):
                send_com += " " + rpt_com

            if len(send_com) > 0:
                try:
                    self.skel_conn.push_comment(head, send_com)
                except Exception as e:
                    g_logger.exception(e)

    def run(self):
        """
            Launch the hooks!
        """
        idaapi.disable_script_timeout()
        if self.skel_settings.initial_sync:
            init_sync = 0
            if AskYN(init_sync, "Do you want to synchronize already defined names?") == 1:
                self.send_names()

            if AskYN(init_sync, "Do you want to synchronize already defined comments?") == 1:
                self.send_comments()

        self.skel_ui.Show()
        self.skel_sync_agent.start()
        self.skel_hooks.hook()

    def setup_terminator(self):
        """
            Register an exit callback
        """
        def end_notify_callback(nw_arg):
            """
                Callback that destroys the object when exiting
            """
            g_logger.debug("Being notified of exiting DB")
            self.end_skelenox()
        idaapi.notify_when(idaapi.NW_CLOSEIDB | idaapi.NW_TERMIDA,
                           end_notify_callback)

    def end_skelenox(self):
        """
            cleanup
        """
        self.skel_conn.close_connection()
        if self.skel_hooks is not None:
            self.skel_hooks.cleanup_hooks()

        self.skel_sync_agent.kill()
        self.skel_sync_agent.skel_conn.close_connection()
        self.skel_sync_agent.join()
        self.skel_ui.Close()

        g_logger.info("Skelenox terminated")


def launch_skelenox():
    """
        Create the instance and launch it
    """
    skelenox = SkelCore("skelsettings.json")
    skelenox.run()
    return skelenox


def PLUGIN_ENTRY():
    """
        IDAPython plugin wrapper
    """
    Wait()
    return SkelenoxPlugin()


class SkelenoxPlugin(idaapi.plugin_t):
    """
        Classic IDAPython plugin
    """
    flags = idaapi.PLUGIN_UNL
    comment = "Skelenox"
    help = "Polichombr synchronization agent"
    wanted_name = "Skelenox"
    wanted_hotkey = "Ctrl-F4"
    skel_object = None

    def init(self):
        """
        IDA plugin init
        """
        self.icon_id = 0
        self.skel_object = launch_skelenox()

        return idaapi.PLUGIN_OK

    def run(self, arg=0):
        return

    def term(self):
        self.skel_object.end_skelenox()


if __name__ == '__main__':
    # RUN !
    skel = launch_skelenox()
