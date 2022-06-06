import json
import math

from odoo import http, _, exceptions
from odoo.http import request

from .serializers import Serializer
from .exceptions import QueryFormatError


def error_response(error, msg):
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": 200,
            "message": msg,
            "data": {
                "name": str(error),
                "debug": "",
                "message": msg,
                "arguments": list(error.args) if hasattr(error, "args") else "",
                "exception_type": type(error).__name__,
            },
        },
    }


class OdooAPI(http.Controller):
    @http.route("/auth/", type="json", auth="none", methods=["POST"], csrf=False)
    def authenticate(self, *args, **post):
        try:
            login = post["login"]
        except KeyError:
            raise exceptions.AccessDenied(message=_("`login` is required."))

        try:
            password = post["password"]
        except KeyError:
            raise exceptions.AccessDenied(message=_("`password` is required."))

        try:
            db = post["db"]
        except KeyError:
            raise exceptions.AccessDenied(message=_("`db` is required."))

        http.request.session.authenticate(db, login, password)
        res = request.env["ir.http"].session_info()
        return res

    @http.route("/reset_password/", type="json", auth="none", methods=["POST"], csrf=False)
    def reset_password(self, *args, **post):
        try:
            login = post["login"]
        except KeyError:
            raise exceptions.AccessDenied(message="`login` is required.")
        user_obj = request.env["res.users"].sudo()
        user = user_obj.search([("login", "=", login), ("active", "=", True)])
        if not user:
            raise exceptions.AccessDenied(message=_("User with given login '%s' does not exist.'") % login)

        # Enable Reset:
        config_obj = request.env["ir.config_parameter"].sudo()
        param = config_obj.get_param("auth_signup.reset_password", "")
        if str(param).lower() != "true":
            raise exceptions.AccessDenied(
                message=_(
                    "The option to reset password is not enabled at the moment. Kindly contact your administrator."
                )
            )
        try:
            user_obj = request.env.user
            user_obj.reset_password(login)
        except Exception as e:
            res = error_response(e, str(e))
            return http.Response(json.dumps(res), status=200, mimetype="application/json")
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": False,
                "result": "ok",
            }
        )

    @http.route("/change_password/", type="json", auth="user", methods=["POST"], csrf=False)
    def change_password(self, *args, **post):
        try:
            current_password = post["current_password"]
        except KeyError:
            raise exceptions.AccessDenied(
                message=_("`current_password` is required."),
            )
        try:
            new_password = post["new_password"]
        except KeyError:
            raise exceptions.AccessDenied(
                message=_("`new_password` is required."),
            )
        # Note: request.env.user has other enviroment with uid=1
        # Check credentials
        user = request.env["res.users"].browse(request.env.uid)
        if not user:
            raise exceptions.AccessDenied(
                message=_("User not logged in."),
            )
        try:
            user.change_password(current_password, new_password)
        except Exception as e:
            raise exceptions.AccessDenied(message=str(e))
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": False,
                "result": "ok",
            }
        )

    @http.route("/object/<string:model>/<string:function>", type="json", auth="user", methods=["POST"], csrf=False)
    def call_model_function(self, model, function, **post):
        args = []
        kwargs = {}
        if "args" in post:
            args = post["args"]
        if "kwargs" in post:
            kwargs = post["kwargs"]
        model = request.env[model]
        result = getattr(model, function)(*args, **kwargs)
        return result

    @http.route(
        "/object/<string:model>/<int:rec_id>/<string:function>", type="json", auth="user", methods=["POST"], csrf=False
    )
    def call_obj_function(self, model, rec_id, function, **post):
        args = []
        kwargs = {}
        if "args" in post:
            args = post["args"]
        if "kwargs" in post:
            kwargs = post["kwargs"]
        obj = request.env[model].browse(rec_id).ensure_one()
        result = getattr(obj, function)(*args, **kwargs)
        return result

    @http.route("/api/<string:model>", type="http", auth="user", methods=["GET"], csrf=False)
    def get_model_data(self, model, **params):
        if "context" in params:
            context = json.loads(params["context"])
            request_env = request.env[model].with_context(**context)
        else:
            request_env = request.env[model]
        try:
            records = request_env.search([])
        except KeyError as e:
            msg = _("The model `%s` does not exist.") % model
            res = error_response(e, msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        if "query" in params:
            query = params["query"]
        else:
            query = "{*}"

        if "order" in params:
            orders = json.loads(params["order"])
        else:
            orders = ""

        if "filter" in params:
            filters = json.loads(params["filter"])
            records = request_env.search(filters, order=orders)

        prev_page = None
        next_page = None
        total_page_number = 1
        current_page = 1

        if "page_size" in params:
            page_size = int(params["page_size"])
            count = len(records)
            total_page_number = math.ceil(count / page_size)

            if "page" in params:
                current_page = int(params["page"])
            else:
                current_page = 1  # Default page Number
            start = page_size * (current_page - 1)
            stop = current_page * page_size
            records = records[start:stop]
            next_page = current_page + 1 if 0 < current_page + 1 <= total_page_number else None
            prev_page = current_page - 1 if 0 < current_page - 1 <= total_page_number else None

        if "limit" in params:
            limit = int(params["limit"])
            records = records[0:limit]

        try:
            serializer = Serializer(records, query, many=True)
            data = serializer.data
        except (SyntaxError, QueryFormatError) as e:
            res = error_response(e, e.msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        res = {
            "count": len(records),
            "prev": prev_page,
            "current": current_page,
            "next": next_page,
            "total_pages": total_page_number,
            "result": data,
        }
        return http.Response(json.dumps(res), status=200, mimetype="application/json")

    @http.route("/api/<string:model>/<int:rec_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def get_model_rec(self, model, rec_id, **params):
        try:
            records = request.env[model].search([])
        except KeyError as e:
            msg = _("The model `%s` does not exist.") % model
            res = error_response(e, msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        if "query" in params:
            query = params["query"]
        else:
            query = "{*}"

        # TODO: Handle the error raised by `ensure_one`
        record = records.browse(rec_id).ensure_one()
        if not record.exists():
            msg = _("The record `%s` does not exist.") % rec_id
            res = error_response(_("Record does not exist."), msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        try:
            serializer = Serializer(record, query)
            data = serializer.data
        except (SyntaxError, QueryFormatError) as e:
            res = error_response(e, e.msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        return http.Response(json.dumps(data), status=200, mimetype="application/json")

    @http.route("/api/<string:model>/", type="json", auth="user", methods=["POST"], csrf=False)
    def post_model_data(self, model, **post):
        try:
            data = post["data"]
        except KeyError:
            msg = _("`data` parameter is not found on POST request body")
            raise exceptions.ValidationError(msg)

        try:
            model_to_post = request.env[model]
        except KeyError:
            msg = _("The model `%s` does not exist.") % model
            raise exceptions.ValidationError(msg)

        # TODO: Handle data validation

        if "context" in post:
            context = post["context"]
            record = model_to_post.with_context(**context).create(data)
        else:
            record = model_to_post.create(data)
        if type(data) == list:
            return record.ids
        return record.id

    # This is for single record update
    @http.route("/api/<string:model>/<int:rec_id>/", type="json", auth="user", methods=["PUT"], csrf=False)
    def put_model_record(self, model, rec_id, **post):
        try:
            data = post["data"]
        except KeyError:
            msg = _("`data` parameter is not found on PUT request body")
            raise exceptions.ValidationError(msg)

        try:
            model_to_put = request.env[model]
        except KeyError:
            msg = _("The model `%s` does not exist.") % model
            raise exceptions.ValidationError(msg)

        if "context" in post:
            # TODO: Handle error raised by `ensure_one`
            rec = model_to_put.with_context(**post["context"]).browse(rec_id).ensure_one()
        else:
            rec = model_to_put.browse(rec_id).ensure_one()

        # TODO: Handle data validation
        for field in data:
            if isinstance(data[field], dict):
                operations = []
                for operation in data[field]:
                    if operation == "push":
                        operations.extend((4, rec_id, _) for rec_id in data[field].get("push"))
                    elif operation == "pop":
                        operations.extend((3, rec_id, _) for rec_id in data[field].get("pop"))
                    elif operation == "delete":
                        operations.extend((2, rec_id, _) for rec_id in data[field].get("delete"))
                    else:
                        data[field].pop(operation)  # Invalid operation

                data[field] = operations
            elif isinstance(data[field], list):
                data[field] = [(6, _, data[field])]  # Replace operation
            else:
                pass

        try:
            return rec.write(data)
        except Exception:
            # TODO: Return error message(e.msg) on a response
            return False

    # This is for bulk update
    @http.route("/api/<string:model>/", type="json", auth="user", methods=["PUT"], csrf=False)
    def put_model_records(self, model, **post):
        try:
            data = post["data"]
        except KeyError:
            msg = _("`data` parameter is not found on PUT request body")
            raise exceptions.ValidationError(msg)

        try:
            model_to_put = request.env[model]
        except KeyError:
            msg = _("The model `%s` does not exist.") % model
            raise exceptions.ValidationError(msg)

        # TODO: Handle errors on filter
        filters = post["filter"]

        if "context" in post:
            recs = model_to_put.with_context(**post["context"]).search(filters)
        else:
            recs = model_to_put.search(filters)

        # TODO: Handle data validation
        for field in data:
            if isinstance(data[field], dict):
                operations = []
                for operation in data[field]:
                    if operation == "push":
                        operations.extend((4, rec_id, _) for rec_id in data[field].get("push"))
                    elif operation == "pop":
                        operations.extend((3, rec_id, _) for rec_id in data[field].get("pop"))
                    elif operation == "delete":
                        operations.extend((2, rec_id, _) for rec_id in data[field].get("delete"))
                    else:
                        pass  # Invalid operation

                data[field] = operations
            elif isinstance(data[field], list):
                data[field] = [(6, _, data[field])]  # Replace operation
            else:
                pass

        if recs.exists():
            try:
                return recs.write(data)
            except Exception:
                # TODO: Return error message(e.msg) on a response
                return False
        else:
            # No records to update
            return True

    # This is for deleting one record
    @http.route("/api/<string:model>/<int:rec_id>/", type="http", auth="user", methods=["DELETE"], csrf=False)
    def delete_model_record(self, model, rec_id, **post):
        try:
            model_to_del_rec = request.env[model]
        except KeyError as e:
            msg = _("The model `%s` does not exist.") % model
            res = error_response(e, msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        # TODO: Handle error raised by `ensure_one`
        rec = model_to_del_rec.browse(rec_id).ensure_one()

        try:
            is_deleted = rec.unlink()
            res = {"result": is_deleted}
            return http.Response(json.dumps(res), status=200, mimetype="application/json")
        except Exception as e:
            res = error_response(e, str(e))
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

    # This is for bulk deletion
    @http.route("/api/<string:model>/", type="http", auth="user", methods=["DELETE"], csrf=False)
    def delete_model_records(self, model, **post):
        filters = json.loads(post["filter"])

        try:
            model_to_del_rec = request.env[model]
        except KeyError as e:
            msg = _("The model `%s` does not exist.") % model
            res = error_response(e, msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        # TODO: Handle error raised by `filters`
        recs = model_to_del_rec.search(filters)

        try:
            is_deleted = recs.unlink()
            res = {"result": is_deleted}
            return http.Response(json.dumps(res), status=200, mimetype="application/json")
        except Exception as e:
            res = error_response(e, str(e))
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

    @http.route(
        "/api/<string:model>/<int:rec_id>/<string:field>", type="http", auth="user", methods=["GET"], csrf=False
    )
    def get_binary_record(self, model, rec_id, field, **post):
        try:
            request.env[model]
        except KeyError as e:
            msg = _("The model `%s` does not exist.") % model
            res = error_response(e, msg)
            return http.Response(json.dumps(res), status=200, mimetype="application/json")

        rec = request.env[model].browse(rec_id).ensure_one()
        if rec.exists():
            src = getattr(rec, field).decode("utf-8")
        else:
            src = False
        return http.Response(src)
