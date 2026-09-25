"""路由注册。"""

from . import actions, cluster, resources


def register(app):
    app.register_blueprint(cluster.bp)
    app.register_blueprint(resources.bp)
    app.register_blueprint(actions.bp)
