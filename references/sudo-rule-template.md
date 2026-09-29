# GTX Broker Profile Switch Sudo Rule

## Purpose

Allow the unprivileged broker service to trigger profile switches via root-owned wrapper script without requiring password entry.

## Installation

### 1. Create sudoers file

```bash
cat > /etc/sudoers.d/gtx-broker-profile-switch << 'EOF'
# Allow gtx-broker service to switch P40 profiles
andyfied ALL=(root) NOPASSWD: /usr/local/sbin/compute01-maint/p40-switch-profile
EOF

# Set correct permissions (must be 0440)
chmod 0440 /etc/sudoers.d/gtx-broker-profile-switch
```

### 2. Validate syntax

```bash
sudo visudo -c -f /etc/sudoers.d/gtx-broker-profile-switch
```

Expected output:
```
/etc/sudoers.d/gtx-broker-profile-switch: parsed OK
```

## Security Considerations

### Narrow scope

- **Command**: Only `/usr/local/sbin/compute01-maint/p40-switch-profile`
- **User**: Only `andyfied` (or service user running gtx-broker)
- **No arguments allowed**: The wrapper itself validates profile names

This prevents:
- Arbitrary command execution
- Privilege escalation via shell injection
- Profile switching to non-allowlisted profiles

### Wrapper validation

The wrapper script must:

1. **Validate profile name**: Only allow `qwen35-coding`, `qwen35-vision`, `qwen36-vision`
2. **Validate config file exists**: Prevent symlink to arbitrary paths
3. **Atomic operations**: Use `ln -sfn` for atomic symlink update
4. **Verify after switch**: Health check, model verification, smoke test

See `scripts/p40-switch-profile` for implementation.

## Testing

### Manual test

```bash
# Test as unprivileged user
sudo -n /usr/local/sbin/compute01-maint/p40-switch-profile qwen35-vision
```

Expected output:
```
[INFO] P40 profile switch started
[INFO] Validated profile: qwen35-vision
[INFO] Updating active config symlink to: qwen35-vision.conf
[INFO] Active config updated successfully
[INFO] Restarting llama-qwen35.service
...
[INFO] P40 profile switch completed successfully: qwen35-vision
```

### Test invalid profile

```bash
sudo -n /usr/local/sbin/compute01-maint/p40-switch-profile invalid-profile
```

Expected: Exit code 1 with error message.

## Updating

If you need to add more profiles:

1. **Add to wrapper allowlist**: Update the `allowed_profiles` array in `scripts/p40-switch-profile`
2. **Create profile config**: Add new `.conf` file in `/etc/llama-cpp/profiles/`
3. **No sudoers change needed**: The sudo rule allows all profiles via the wrapper

## Troubleshooting

### "user is not in the sudoers file"

**Cause**: User doesn't match sudoers entry.

**Fix**: Check username in sudoers file matches actual user running gtx-broker.

```bash
whoami
cat /etc/sudoers.d/gtx-broker-profile-switch
```

### "command not found"

**Cause**: Wrapper script path incorrect or doesn't exist.

**Fix**: Verify wrapper location.

```bash
ls -la /usr/local/sbin/compute01-maint/p40-switch-profile
```

### "permission denied" on sudoers file

**Cause**: File permissions not 0440.

**Fix**:
```bash
chmod 0440 /etc/sudoers.d/gtx-broker-profile-switch
```

### Sudoers syntax error

**Cause**: Invalid syntax in sudoers file.

**Fix**: Use `visudo` to check syntax.

```bash
sudo visudo -c -f /etc/sudoers.d/gtx-broker-profile-switch
```

Common errors:
- Missing `=` signs
- Wrong indentation (spaces, not tabs)
- Missing `NOPASSWD:` keyword
- Path doesn't match exactly (including slashes)

## Alternative Approaches

### Option 1: Run broker as root (not recommended)

```ini
[Service]
User=root
```

**Security risk**: Broker process has full root access.

### Option 2: Use `setuid` binary (not recommended)

```bash
sudo setuid /path/to/broker-binary
```

**Security risk**: Complex to implement correctly, easy to introduce bugs.

### Option 3: Custom systemd service (recommended alternative)

```ini
[Unit]
Description=P40 profile switch service

[Service]
Type=oneshot
User=root
ExecStart=/usr/local/sbin/compute01-maint/p40-switch-profile %i
```

**Benefits**:
- No sudo needed
- Better isolation
- Easier logging via systemd journal

**Drawbacks**:
- More complex setup
- Need to manage D-Bus or socket activation

## References

- **Wrapper script**: `scripts/p40-switch-profile`
- **Deployment notes**: `docs/deployment-notes.md`
- **Sudoers documentation**: `man sudoers`
