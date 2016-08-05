# opalescense
###### (yes, it's intentionally spelled wrong, sure)

One day this project will grow to be a humble, yet useful torrent client.
A torrent client that strives not to impress, but rather to just...exist.

Currently, it's a glorified torrent creation machine.

#TODO
- [x] refactor torrent creation from path
- [x] simplify torrent translation lifecycle (textfile -> pyobject -> Torrent)
- [x] more effective torrent dictionary verification
- [ ] get rid of dependency on weird config.py file
- [ ] better tests


### add functionality for:
- [ ] specifying path/dir from which to create .torrent from cli
- [ ] specifying path to which to save .torrent
- [ ] specifying .torrent name
- [ ] specifying optional .torrent options (piece_size, etc)


#### far in the future functionality:
- [ ] diffing between Torrents?
- [ ] tracker comm
- [ ] ui?
