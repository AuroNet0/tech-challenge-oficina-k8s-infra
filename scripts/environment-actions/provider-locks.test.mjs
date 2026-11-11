import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const platforms = {
  windows_amd64: {
    content: 'h1:H3mU/7URhP0uCRGK8jeQRKxx2XFzEqLiOq/L2Bbiaxs=',
    archive: 'zh:9f8b909d3ec50ade83c8062290378b1ec553edef6a447c56dadc01a99f4eaa93',
  },
  linux_amd64: {
    content: 'h1:edXOJWE4ORX8Fm+dpVpICzMZJat4AX0VRCAy/xkcOc0=',
    archive: 'zh:1589a2266af699cbd5d80737a0fe02e54ec9cf2ca54e7e00ac51c7359056f274',
  },
};

for (const directory of ['bootstrap/identities', 'stacks/infra', 'stacks/platform', 'stacks/edge']) {
  test(`${directory} pins signed AWS 5.100.0 checksums for Windows and Linux amd64`, () => {
    const root = new URL(`../../phase4/${directory}/`, import.meta.url);
    const lock = fs.readFileSync(new URL('.terraform.lock.hcl', root), 'utf8');
    const versions = fs.readFileSync(new URL('versions.tf', root), 'utf8');
    assert.deepEqual([...lock.matchAll(/provider "([^"]+)"/g)].map(match => match[1]), ['registry.terraform.io/hashicorp/aws']);
    assert.match(lock, /version\s*=\s*"5\.100\.0"/);
    assert.match(lock, /constraints\s*=\s*"5\.100\.0"/);
    assert.match(versions, /version\s*=\s*"= 5\.100\.0"/);
    for (const [platform, hashes] of Object.entries(platforms)) {
      for (const hash of Object.values(hashes)) assert.ok(lock.includes(`"${hash}"`), `${platform}: ${hash}`);
    }
    const bootstrapLock = fs.readFileSync(new URL('../../phase4/bootstrap/identities/.terraform.lock.hcl', import.meta.url), 'utf8');
    assert.equal(lock.replace(/\r\n/g, '\n'), bootstrapLock.replace(/\r\n/g, '\n'));
  });
}
