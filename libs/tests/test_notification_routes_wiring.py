from services.processing.app.v2.api.router.notification_routes import router


def test_router_is_mounted_under_notifications():
    assert router.prefix == "/notifications"


def test_every_documented_endpoint_exists():
    paths = {(r.path, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in router.routes}
    assert ("/notifications/ticket", ("POST",)) in paths
    assert ("/notifications/stream", ("GET",)) in paths
    assert ("/notifications", ("GET",)) in paths
    assert ("/notifications/unread-count", ("GET",)) in paths
    assert ("/notifications/{notification_id}/read", ("POST",)) in paths
    assert ("/notifications/read-all", ("POST",)) in paths


def test_read_all_is_declared_before_the_id_route():
    """Otherwise /read-all matches /{notification_id}/read patterns first."""
    order = [r.path for r in router.routes]
    assert order.index("/notifications/read-all") < order.index(
        "/notifications/{notification_id}/read"
    )
