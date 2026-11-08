"""Execute exactly one reviewed packet plan, stopping at the first failure."""
import time


class ProtocolError(RuntimeError):
    pass


def execute(plan, transport, sleep=time.sleep):
    completed = 0
    for step in plan:
        try:
            if step.channel == 'feature':
                sleep(step.delay_ms / 1000)
                transport.send_feature(step.data)
                if step.ack:
                    sleep(step.delay_ms / 1000)
                    response = transport.get_feature()
                    if len(response) != 64 or response[3] != 1:
                        status = response[3] if len(response) > 3 else None
                        raise ProtocolError(f'确认状态异常: {status!r}')
            else:
                sleep(step.delay_ms / 1000)
                transport.short_exchange(step.data)
        except (OSError, ProtocolError) as e:
            raise ProtocolError(f'步骤 {completed + 1}「{step.label}」失败，已完成 {completed} 步: {e}；设备可能处于部分更新状态') from e
        completed += 1
    return completed
