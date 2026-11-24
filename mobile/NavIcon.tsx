import React from 'react';
import {Image, type ImageSourcePropType} from 'react-native';

const images: Record<string, ImageSourcePropType> = {
  home: require('./assets/navigation/home.png'),
  settings: require('./assets/navigation/settings.png'),
  messages: require('./assets/navigation/messages.png'),
  friends: require('./assets/navigation/friends.png'),
  world: require('./assets/navigation/world.png'),
  room: require('./assets/navigation/room.png'),
  watch: require('./assets/navigation/watch.png'),
  games: require('./assets/navigation/games.png'),
  queue: require('./assets/navigation/queue.png'),
  add: require('./assets/navigation/add.png'),
  back: require('./assets/navigation/back.png'),
  profile: require('./assets/navigation/profile.png'),
  close: require('./assets/navigation/close.png'),
  pause: require('./assets/navigation/pause.png'),
  fullscreen: require('./assets/navigation/fullscreen.png'),
  exitFullscreen: require('./assets/navigation/exit-fullscreen.png'),
  attach: require('./assets/navigation/attach.png'),
};

const names: Record<string,string> = {
  Home:'home', Settings:'settings', 'Server settings':'settings', Messages:'messages',
  Friends:'friends', 'Open World':'world', World:'world', Watch:'watch', Queue:'queue',
  Games:'games', Room:'room', Profile:'profile', 'Your profile & history':'profile',
  Play:'watch', Pause:'pause', Fullscreen:'fullscreen', 'Exit fullscreen':'exitFullscreen',
  '← Back':'back', Back:'back', '＋ Add video':'add', Close:'close', '×':'close', '▧':'attach',
};

export function NavIcon({label,color,size=18}:{label:string;color:string;size?:number}) {
  const icon=images[names[label]];
  return icon ? <Image source={icon} accessible={false} style={{width:size,height:size,tintColor:color}}/> : null;
}

export function navLabel(label:string) {
  return ({'← Back':'Back','＋ Add video':'Add video','×':'Close','▧':'Attach image'} as Record<string,string>)[label] || label;
}
