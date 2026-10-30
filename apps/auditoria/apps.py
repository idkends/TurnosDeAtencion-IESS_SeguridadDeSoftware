from django.apps import AppConfig


class AuditoriaConfig(AppConfig):
    name = "apps.auditoria"
    label = "auditoria"
    verbose_name = "Auditoria"

    def ready(self):
        from config.endpoints import limite_excedido

        from . import servicios

        def _al_exceder(sender, ambito, usuario_id=None, **kwargs):
            servicios.registrar(
                accion=servicios.Acciones.LIMITE_EXCEDIDO, actor_id=usuario_id, resultado=servicios.DENEGADO, detalle={"ambito": ambito}
            )

        limite_excedido.connect(_al_exceder, dispatch_uid="auditoria.limite_excedido", weak=False)
